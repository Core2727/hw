"""Export API endpoints: execute a query and export the result to CSV/JSON."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, select
from app.database import get_session
from app.models.database import DatabaseConnection
from app.models.query import QuerySource
from app.models.schemas import (
    ExportRequest,
    ExportResponse,
    ExportFileInfo,
    QueryResult,
)
from app.services.query_wrapper import execute_query_with_service
from app.services.sql_validator import SqlValidationError
from app.services.export_service import export_service

router = APIRouter(prefix="/api/v1/dbs", tags=["exports"])


async def _get_connection_or_404(session: Session, name: str) -> DatabaseConnection:
    """Fetch a database connection or raise 404."""
    statement = select(DatabaseConnection).where(DatabaseConnection.name == name)
    connection = session.exec(statement).first()
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database connection '{name}' not found",
        )
    return connection


@router.post("/{name}/query/export", response_model=ExportResponse)
async def export_query_result(
    name: str,
    input_data: ExportRequest,
    session: Session = Depends(get_session),
) -> ExportResponse:
    """
    Execute a SQL query and export the result to a CSV or JSON file.

    The query runs through the same validation/execution path as the
    normal query endpoint (SELECT-only, LIMIT injected when missing),
    then the full result set is serialized and written to the server's
    exports directory. The response contains file metadata plus a
    download URL.
    """
    connection = await _get_connection_or_404(session, name)

    try:
        result: QueryResult = await execute_query_with_service(
            session,
            name,
            connection.db_type,
            connection.url,
            input_data.sql,
            QuerySource.MANUAL,
        )
    except SqlValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {str(e)}",
        )

    try:
        return export_service.export(
            database_name=name,
            sql=result.sql,
            fmt=input_data.format,
            columns=[col.model_dump(by_alias=True) for col in result.columns],
            rows=result.rows,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write export file: {str(e)}",
        )


@router.get("/{name}/exports", response_model=list[ExportFileInfo])
async def list_exports(
    name: str,
    session: Session = Depends(get_session),
) -> list[ExportFileInfo]:
    """List previously exported files for a database connection."""
    await _get_connection_or_404(session, name)
    items = export_service.list_exports(name)
    return [ExportFileInfo(**item) for item in items]


@router.get("/{name}/exports/{filename}")
async def download_export(
    name: str,
    filename: str,
    session: Session = Depends(get_session),
) -> Response:
    """Download a previously exported file."""
    await _get_connection_or_404(session, name)

    path = export_service.get_file_path(name, filename)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export file '{filename}' not found",
        )

    media_type = "text/csv" if path.suffix == ".csv" else "application/json"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
    )
