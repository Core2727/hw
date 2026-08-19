"""Export service: serialize query results to CSV/JSON files.

Writes exported files to a server-side exports directory (one per
database connection) and returns metadata about the written file.
A JSON sidecar file (``<filename>.meta.json``) records the SQL,
row count, and timestamp so exports are self-documenting.
"""

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.schemas import ExportResponse

logger = logging.getLogger(__name__)

# Unsafe filesystem characters collapsed to underscore
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str) -> str:
    """Make a string safe for use in a filename."""
    cleaned = _FILENAME_SAFE.sub("_", name).strip("._-")
    return cleaned or "export"


def _format_timestamp(dt: datetime) -> str:
    """Format a datetime as a filesystem-safe timestamp (to the minute)."""
    return dt.strftime("%Y%m%dT%H%M%S")


def _row_to_csv_value(value: Any) -> str:
    """Convert a single cell value to its CSV string representation."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class ExportService:
    """Serialize QueryResult data to CSV/JSON files on disk."""

    def __init__(self, exports_dir: Path | None = None):
        """Initialize with an optional exports directory override (for tests)."""
        self.exports_dir = exports_dir or self._default_exports_dir()
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _default_exports_dir() -> Path:
        data_dir = Path(settings.db_query_data_dir).expanduser()
        return data_dir / "exports"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize_csv(self, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
        """Serialize columns/rows to a CSV string (UTF-8 with BOM)."""
        header_names = [col["name"] for col in columns]
        buffer = io.StringIO()
        # BOM lets Excel auto-detect UTF-8 (Chinese columns won't garble)
        buffer.write("﻿")
        writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header_names)
        for row in rows:
            writer.writerow([_row_to_csv_value(row.get(name)) for name in header_names])
        return buffer.getvalue()

    def serialize_json(self, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
        """Serialize rows to a pretty-printed JSON array string."""
        payload = {
            "columns": columns,
            "rowCount": len(rows),
            "rows": rows,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def serialize(
        self, fmt: str, columns: list[dict[str, Any]], rows: list[dict[str, Any]]
    ) -> str:
        """Dispatch serialization by format ('csv' or 'json')."""
        if fmt == "csv":
            return self.serialize_csv(columns, rows)
        if fmt == "json":
            return self.serialize_json(columns, rows)
        raise ValueError(f"Unsupported export format: {fmt}")

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def build_filename(self, database_name: str, fmt: str, timestamp: datetime) -> str:
        """Build the export filename: <db>_<timestamp>.<fmt>."""
        return f"{_sanitize(database_name)}_{_format_timestamp(timestamp)}.{fmt}"

    def export(
        self,
        database_name: str,
        sql: str,
        fmt: str,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> ExportResponse:
        """Serialize query results, write to disk, and return metadata.

        Raises:
            ValueError: If fmt is not 'csv' or 'json'
        """
        content = self.serialize(fmt, columns, rows)
        exported_at = datetime.now(timezone.utc).replace(tzinfo=None)
        filename = self.build_filename(database_name, fmt, exported_at)

        target_dir = self.exports_dir / _sanitize(database_name)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / filename
        file_path.write_text(content, encoding="utf-8")

        # Sidecar metadata so the file is self-documenting
        meta_path = target_dir / f"{filename}.meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "databaseName": database_name,
                    "sql": sql,
                    "format": fmt,
                    "rowCount": len(rows),
                    "exportedAt": exported_at.isoformat() + "Z",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        file_size = file_path.stat().st_size
        logger.info(
            f"Exported {len(rows)} rows to {file_path} ({file_size} bytes, format={fmt})"
        )

        return ExportResponse(
            filename=filename,
            format=fmt,
            databaseName=database_name,
            sql=sql,
            rowCount=len(rows),
            filePath=str(file_path),
            fileSizeBytes=file_size,
            exportedAt=exported_at,
            downloadUrl=f"/api/v1/dbs/{database_name}/exports/{filename}",
        )

    # ------------------------------------------------------------------
    # Listing / retrieval
    # ------------------------------------------------------------------

    def list_exports(self, database_name: str | None = None) -> list[dict[str, Any]]:
        """List export files (optionally filtered by database) with sidecar metadata."""
        base = self.exports_dir / _sanitize(database_name) if database_name else self.exports_dir
        if not base.exists():
            return []

        results: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*.csv")):
            results.append(self._file_info(path))
        for path in sorted(base.rglob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            results.append(self._file_info(path))
        results.sort(key=lambda item: item.get("exportedAt") or "", reverse=True)
        return results

    def _file_info(self, path: Path) -> dict[str, Any]:
        """Read sidecar metadata (if present) plus file stats for one export file."""
        info: dict[str, Any] = {
            "filename": path.name,
            "format": path.suffix.lstrip("."),
            "databaseName": path.parent.name,
            "rowCount": None,
            "fileSizeBytes": path.stat().st_size,
            "exportedAt": None,
            "sql": None,
        }
        meta_path = path.with_name(f"{path.name}.meta.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                info.update(
                    {
                        "databaseName": meta.get("databaseName", info["databaseName"]),
                        "rowCount": meta.get("rowCount"),
                        "exportedAt": meta.get("exportedAt"),
                        "sql": meta.get("sql"),
                    }
                )
            except (json.JSONDecodeError, OSError):
                logger.warning(f"Failed to read export sidecar: {meta_path}")
        return info

    def get_file_path(self, database_name: str, filename: str) -> Path | None:
        """Resolve an export file path after validating the filename is safe."""
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return None
        path = self.exports_dir / _sanitize(database_name) / Path(filename).name
        return path if path.is_file() else None


# Global service instance (lazily created so tests can monkeypatch settings)
export_service = ExportService()
