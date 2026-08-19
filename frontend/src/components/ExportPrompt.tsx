/** Export prompt notification shown after a successful query. */

import React from "react";
import { Button, Space, Typography } from "antd";
import {
  FileTextOutlined,
  DownOutlined,
  CloseOutlined,
} from "@ant-design/icons";

const { Text } = Typography;

interface ExportPromptProps {
  rowCount: number;
  onExport: (format: "csv" | "json") => void;
  onDismiss: () => void;
  busy?: boolean;
}

/**
 * Inline card that asks the user, right after a query completes,
 * whether they want to export the result as CSV or JSON.
 * Rendered inside an antd notification so it appears proactively
 * without blocking the results table.
 */
export const ExportPrompt: React.FC<ExportPromptProps> = ({
  rowCount,
  onExport,
  onDismiss,
  busy = false,
}) => (
  <div>
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Space size={6}>
        <DownOutlined style={{ color: "#16AA98" }} />
        <Text>
          Query returned {rowCount} rows. Export this result to a file?
        </Text>
      </Space>
      <Space size={8}>
        <Button
          size="small"
          icon={<FileTextOutlined />}
          loading={busy}
          onClick={() => onExport("csv")}
          style={{ fontWeight: 700 }}
        >
          EXPORT CSV
        </Button>
        <Button
          size="small"
          icon={<FileTextOutlined />}
          loading={busy}
          onClick={() => onExport("json")}
          style={{ fontWeight: 700 }}
        >
          EXPORT JSON
        </Button>
        <Button size="small" type="text" icon={<CloseOutlined />} onClick={onDismiss} />
      </Space>
      <Text type="secondary" style={{ fontSize: 12 }}>
        Files are saved on the server (with SQL metadata) and downloaded to your
        browser.
      </Text>
    </Space>
  </div>
);
