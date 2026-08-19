/** Export-related API helpers (server-side export endpoints). */

import { apiClient } from "./api";

export interface ExportResultInfo {
  filename: string;
  format: string;
  databaseName: string;
  sql: string;
  rowCount: number;
  filePath: string;
  fileSizeBytes: number;
  exportedAt: string;
  downloadUrl: string;
}

export interface ExportFileInfo {
  filename: string;
  format: string;
  databaseName: string;
  rowCount?: number | null;
  fileSizeBytes: number;
  exportedAt?: string | null;
  sql?: string | null;
}

export type ExportFormat = "csv" | "json";

/**
 * Execute a query on the server and export the result as a file.
 * Returns file metadata; the file is also persisted server-side.
 */
export async function exportQuery(
  databaseName: string,
  sql: string,
  format: ExportFormat
): Promise<ExportResultInfo> {
  const response = await apiClient.post<ExportResultInfo>(
    `/api/v1/dbs/${databaseName}/query/export`,
    { sql, format }
  );
  return response.data;
}

/**
 * Trigger a browser download for an already-exported file
 * via its download URL (relative to the API base URL).
 */
export async function downloadExport(
  databaseName: string,
  filename: string
): Promise<void> {
  const response = await apiClient.get(
    `/api/v1/dbs/${databaseName}/exports/${filename}`,
    { responseType: "blob" }
  );
  const url = URL.createObjectURL(response.data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** List previously exported files for a database connection. */
export async function listExports(
  databaseName: string
): Promise<ExportFileInfo[]> {
  const response = await apiClient.get<ExportFileInfo[]>(
    `/api/v1/dbs/${databaseName}/exports`
  );
  return response.data;
}
