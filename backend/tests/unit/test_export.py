"""Unit tests for the export feature (service + API endpoints)."""

import csv
import io
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session
from app.models.database import DatabaseConnection, ConnectionStatus, DatabaseType
from app.services.export_service import ExportService, _sanitize
from app.models.schemas import QueryResult, QueryColumn
from app.services.sql_validator import SqlValidationError


@pytest.fixture
def exports_dir(tmp_path: Path) -> Path:
    """Dedicated exports directory per test."""
    return tmp_path / "exports"


@pytest.fixture
def service(exports_dir: Path) -> ExportService:
    return ExportService(exports_dir=exports_dir)


@pytest.fixture
def sample_columns() -> list[dict]:
    return [
        {"name": "id", "dataType": "integer"},
        {"name": "name", "dataType": "character varying"},
        {"name": "balance", "dataType": "double precision"},
    ]


@pytest.fixture
def sample_rows() -> list[dict]:
    return [
        {"id": 1, "name": "Alice", "balance": 10.5},
        {"id": 2, "name": "Bob, Jr.", "balance": None},
        {"id": 3, "name": 'He said "hi"\nnewline', "balance": -0.25},
        {"id": 4, "name": "张三", "balance": 0},
    ]


# ---------------------------------------------------------------------------
# ExportService: serialization
# ---------------------------------------------------------------------------


class TestSerializeCsv:
    def test_csv_has_header_and_all_rows(self, service, sample_columns, sample_rows):
        content = service.serialize_csv(sample_columns, sample_rows)
        # csv.reader handles the quoted embedded newline; splitlines over-counts
        rows = list(csv.reader(content.lstrip("﻿").splitlines()))
        assert rows[0] == ["id", "name", "balance"]
        assert len(rows) == 1 + len(sample_rows)

    def test_csv_bom_prefix_for_excel(self, service, sample_columns, sample_rows):
        content = service.serialize_csv(sample_columns, sample_rows)
        assert content.startswith("﻿")

    def test_csv_escapes_quotes_commas_newlines(self, service, sample_columns, sample_rows):
        content = service.serialize_csv(sample_columns, sample_rows)
        # csv.reader over a StringIO handles the quoted embedded newline
        rows = list(csv.reader(io.StringIO(content.lstrip("﻿"))))
        assert rows[0] == ["id", "name", "balance"]
        assert rows[2][1] == "Bob, Jr."
        assert rows[3][1] == 'He said "hi"\nnewline'
        assert rows[3][2] == "-0.25"

    def test_csv_none_becomes_empty_string(self, service, sample_columns, sample_rows):
        content = service.serialize_csv(sample_columns, sample_rows)
        rows = list(csv.reader(content.lstrip("﻿").splitlines()))
        assert rows[2][2] == ""

    def test_csv_preserves_chinese(self, service, sample_columns, sample_rows):
        content = service.serialize_csv(sample_columns, sample_rows)
        rows = list(csv.reader(content.lstrip("﻿").splitlines()))
        assert rows[4][1] == "张三"


class TestSerializeJson:
    def test_json_structure(self, service, sample_columns, sample_rows):
        payload = json.loads(service.serialize_json(sample_columns, sample_rows))
        assert payload["rowCount"] == 4
        assert payload["columns"] == sample_columns
        assert payload["rows"][0]["name"] == "Alice"

    def test_json_keeps_null(self, service, sample_columns, sample_rows):
        payload = json.loads(service.serialize_json(sample_columns, sample_rows))
        assert payload["rows"][1]["balance"] is None

    def test_json_chinese_not_escaped(self, service, sample_columns, sample_rows):
        content = service.serialize_json(sample_columns, sample_rows)
        assert "张三" in content


class TestSerializeDispatch:
    def test_unknown_format_raises(self, service, sample_columns, sample_rows):
        with pytest.raises(ValueError, match="Unsupported export format"):
            service.serialize("xml", sample_columns, sample_rows)


# ---------------------------------------------------------------------------
# ExportService: file persistence
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_csv_writes_file_and_sidecar(self, service, exports_dir, sample_columns, sample_rows):
        resp = service.export("test_db", "SELECT * FROM users", "csv", sample_columns, sample_rows)
        assert resp.filename.endswith(".csv")
        assert resp.row_count == 4
        assert resp.format == "csv"
        assert resp.database_name == "test_db"
        file_path = Path(resp.file_path)
        assert file_path.is_file()
        assert file_path.parent == exports_dir / "test_db"
        assert resp.file_size_bytes == file_path.stat().st_size
        assert resp.download_url == f"/api/v1/dbs/test_db/exports/{resp.filename}"

        sidecar = file_path.with_name(file_path.name + ".meta.json")
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        assert meta["sql"] == "SELECT * FROM users"
        assert meta["rowCount"] == 4
        assert meta["format"] == "csv"

    def test_export_json_writes_file(self, service, exports_dir, sample_columns, sample_rows):
        resp = service.export("test_db", "SELECT 1", "json", sample_columns, sample_rows)
        assert resp.filename.endswith(".json")
        payload = json.loads(Path(resp.file_path).read_text(encoding="utf-8"))
        assert payload["rowCount"] == 4

    def test_export_creates_missing_directory(self, service, exports_dir, sample_columns, sample_rows):
        assert not (exports_dir / "newdb").exists()
        resp = service.export("newdb", "SELECT 1", "csv", sample_columns, sample_rows)
        assert (exports_dir / "newdb" / resp.filename).is_file()

    def test_export_unsafe_db_name_sanitized(self, service, exports_dir, sample_columns, sample_rows):
        resp = service.export("../evil/db name", "SELECT 1", "csv", sample_columns, sample_rows)
        assert ".." not in resp.file_path
        assert Path(resp.file_path).parent.parent == exports_dir


class TestListAndGet:
    def test_list_exports_empty(self, service):
        assert service.list_exports("test_db") == []

    def test_list_exports_returns_metadata(self, service, sample_columns, sample_rows):
        service.export("test_db", "SELECT a", "csv", sample_columns, sample_rows)
        service.export("test_db", "SELECT b", "json", sample_columns, sample_rows)
        items = service.list_exports("test_db")
        assert len(items) == 2
        formats = {item["format"] for item in items}
        assert formats == {"csv", "json"}
        for item in items:
            assert item["databaseName"] == "test_db"
            assert item["rowCount"] == 4
            assert item["sql"] in ("SELECT a", "SELECT b")

    def test_list_exports_filters_by_database(self, service, sample_columns, sample_rows):
        service.export("db_one", "SELECT 1", "csv", sample_columns, sample_rows)
        service.export("db_two", "SELECT 2", "csv", sample_columns, sample_rows)
        only_one = service.list_exports("db_one")
        assert len(only_one) == 1
        assert only_one[0]["databaseName"] == "db_one"

    def test_list_exports_skips_sidecar_files(self, service, sample_columns, sample_rows):
        service.export("test_db", "SELECT 1", "json", sample_columns, sample_rows)
        items = service.list_exports("test_db")
        assert all(not item["filename"].endswith(".meta.json") for item in items)

    def test_list_all_databases_when_no_filter(self, service, sample_columns, sample_rows):
        service.export("db_one", "SELECT 1", "csv", sample_columns, sample_rows)
        service.export("db_two", "SELECT 2", "csv", sample_columns, sample_rows)
        assert len(service.list_exports()) == 2

    def test_get_file_path_rejects_traversal(self, service, sample_columns, sample_rows):
        service.export("test_db", "SELECT 1", "csv", sample_columns, sample_rows)
        assert service.get_file_path("test_db", "../../etc/passwd") is None
        assert service.get_file_path("test_db", "a/b.csv") is None

    def test_get_file_path_resolves_existing(self, service, sample_columns, sample_rows):
        resp = service.export("test_db", "SELECT 1", "csv", sample_columns, sample_rows)
        path = service.get_file_path("test_db", resp.filename)
        assert path is not None and path.is_file()

    def test_get_file_path_missing_file(self, service):
        assert service.get_file_path("test_db", "nope.csv") is None


class TestSanitize:
    def test_spaces_and_slashes_replaced(self):
        assert _sanitize("my db/v2") == "my_db_v2"

    def test_empty_becomes_export(self):
        assert _sanitize("///") == "export"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def test_session():
    engine = create_engine(
        "sqlite:///file:test_export_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(test_session, exports_dir):
    def get_test_session():
        return test_session

    app.dependency_overrides[get_session] = get_test_session
    # Point the global export service at a temp dir via env override
    with patch("app.api.v1.exports.export_service", ExportService(exports_dir=exports_dir)):
        with TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_connection(test_session):
    conn = DatabaseConnection(
        name="test_db",
        url="postgresql://user:pass@localhost/testdb",
        description="Test database",
        status=ConnectionStatus.ACTIVE,
        db_type=DatabaseType.POSTGRESQL,
    )
    test_session.add(conn)
    test_session.commit()
    return conn


def make_query_result(rows: list[dict] | None = None) -> QueryResult:
    return QueryResult(
        columns=[
            QueryColumn(name="id", dataType="integer"),
            QueryColumn(name="name", dataType="character varying"),
        ],
        rows=rows if rows is not None else [{"id": 1, "name": "Alice"}],
        rowCount=len(rows) if rows is not None else 1,
        executionTimeMs=5,
        sql="SELECT id, name FROM users",
    )


class TestExportEndpoint:
    def test_export_csv_success(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(),
        ):
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT id, name FROM users", "format": "csv"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["format"] == "csv"
        assert body["rowCount"] == 1
        assert body["databaseName"] == "test_db"
        assert body["filename"].endswith(".csv")
        assert body["downloadUrl"].startswith("/api/v1/dbs/test_db/exports/")

    def test_export_json_success(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(),
        ):
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT id, name FROM users", "format": "json"},
            )
        assert response.status_code == 200
        assert response.json()["format"] == "json"

    def test_export_defaults_to_csv(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(),
        ) as mock_exec:
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT 1"},
            )
        assert response.status_code == 200
        assert response.json()["format"] == "csv"
        assert mock_exec.await_count == 1

    def test_export_unknown_database_404(self, client):
        response = client.post(
            "/api/v1/dbs/nope/query/export",
            json={"sql": "SELECT 1", "format": "csv"},
        )
        assert response.status_code == 404

    def test_export_invalid_format_422(self, client, sample_connection):
        response = client.post(
            "/api/v1/dbs/test_db/query/export",
            json={"sql": "SELECT 1", "format": "xml"},
        )
        assert response.status_code == 422

    def test_export_sql_validation_error_400(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            side_effect=SqlValidationError("Only SELECT statements are allowed"),
        ):
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "DELETE FROM users", "format": "csv"},
            )
        assert response.status_code == 400
        assert "SELECT" in response.json()["detail"]

    def test_export_execution_error_500(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT 1", "format": "csv"},
            )
        assert response.status_code == 500

    def test_export_then_download_roundtrip(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(
                [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
            ),
        ):
            export_resp = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT id, name FROM users", "format": "csv"},
            )
        assert export_resp.status_code == 200
        filename = export_resp.json()["filename"]

        download_resp = client.get(f"/api/v1/dbs/test_db/exports/{filename}")
        assert download_resp.status_code == 200
        assert download_resp.headers["content-type"].startswith("text/csv")
        assert b"id,name" in download_resp.content
        assert b"Alice" in download_resp.content and b"Bob" in download_resp.content

    def test_export_empty_result_still_writes_file(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(rows=[]),
        ):
            response = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT id, name FROM users", "format": "csv"},
            )
        assert response.status_code == 200
        assert response.json()["rowCount"] == 0


class TestListAndDownloadEndpoints:
    def test_list_exports_unknown_db_404(self, client):
        assert client.get("/api/v1/dbs/nope/exports").status_code == 404

    def test_list_exports_empty(self, client, sample_connection):
        response = client.get("/api/v1/dbs/test_db/exports")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_after_export(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(),
        ):
            client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT id, name FROM users", "format": "json"},
            )
        response = client.get("/api/v1/dbs/test_db/exports")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["format"] == "json"
        assert items[0]["rowCount"] == 1

    def test_download_missing_file_404(self, client, sample_connection):
        assert client.get("/api/v1/dbs/test_db/exports/ghost.csv").status_code == 404

    def test_download_traversal_blocked(self, client, sample_connection):
        response = client.get("/api/v1/dbs/test_db/exports/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 404

    def test_download_json_content_type(self, client, sample_connection):
        with patch(
            "app.api.v1.exports.execute_query_with_service",
            new_callable=AsyncMock,
            return_value=make_query_result(),
        ):
            export_resp = client.post(
                "/api/v1/dbs/test_db/query/export",
                json={"sql": "SELECT 1", "format": "json"},
            )
        filename = export_resp.json()["filename"]
        response = client.get(f"/api/v1/dbs/test_db/exports/{filename}")
        assert response.headers["content-type"].startswith("application/json")
