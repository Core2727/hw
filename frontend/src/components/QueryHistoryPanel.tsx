/** Query history panel with re-run and export actions. */

import React, { useEffect, useState } from "react";
import {
  Card,
  List,
  Button,
  Space,
  Typography,
  Tag,
  Tooltip,
  Spin,
  Empty,
} from "antd";
import {
  HistoryOutlined,
  PlayCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { apiClient } from "../services/api";
import { QueryHistoryEntry } from "../types/query";

const { Text } = Typography;

interface QueryHistoryPanelProps {
  databaseName: string;
  onRerun: (sql: string) => void;
  onExport: (sql: string, format: "csv" | "json") => void;
  refreshKey?: number;
  exportingSql?: string | null;
}

export const QueryHistoryPanel: React.FC<QueryHistoryPanelProps> = ({
  databaseName,
  onRerun,
  onExport,
  refreshKey = 0,
  exportingSql = null,
}) => {
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const loadHistory = async () => {
    setLoading(true);
    try {
      const response = await apiClient.get<QueryHistoryEntry[]>(
        `/api/v1/dbs/${databaseName}/history`
      );
      setHistory(response.data);
    } catch (error) {
      console.error("Failed to load query history:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databaseName, refreshKey]);

  return (
    <Card
      title={
        <Space>
          <HistoryOutlined />
          <Text
            strong
            style={{
              fontSize: 13,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            QUERY HISTORY
          </Text>
        </Space>
      }
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={loadHistory}
          loading={loading}
          style={{ fontWeight: 700 }}
        >
          REFRESH
        </Button>
      }
      style={{ borderWidth: 2, borderColor: "#000000", marginTop: 16 }}
    >
      {loading && history.length === 0 ? (
        <div style={{ textAlign: "center", padding: 24 }}>
          <Spin />
        </div>
      ) : history.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary" style={{ fontSize: 13 }}>
              No queries executed yet
            </Text>
          }
        />
      ) : (
        <List
          size="small"
          dataSource={history}
          renderItem={(item) => (
            <List.Item
              style={{ padding: "8px 0" }}
              actions={[
                <Tooltip title="Load this SQL into the editor and execute again">
                  <Button
                    key="rerun"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    onClick={() => onRerun(item.sqlText)}
                    style={{ fontWeight: 700 }}
                  >
                    RE-RUN
                  </Button>
                </Tooltip>,
                <Tooltip key="csv" title="Re-execute this SQL and export as CSV">
                  <Button
                    size="small"
                    icon={<FileTextOutlined />}
                    loading={exportingSql === item.sqlText}
                    onClick={() => onExport(item.sqlText, "csv")}
                    style={{ fontWeight: 700 }}
                  >
                    CSV
                  </Button>
                </Tooltip>,
                <Tooltip key="json" title="Re-execute this SQL and export as JSON">
                  <Button
                    size="small"
                    icon={<FileTextOutlined />}
                    loading={exportingSql === item.sqlText}
                    onClick={() => onExport(item.sqlText, "json")}
                    style={{ fontWeight: 700 }}
                  >
                    JSON
                  </Button>
                </Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size={8}>
                    <Tag
                      color={item.success ? "green" : "red"}
                      style={{ fontWeight: 700 }}
                    >
                      {item.success ? "OK" : "FAIL"}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {new Date(item.executedAt).toLocaleString()}
                    </Text>
                    {item.rowCount != null && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {item.rowCount} rows
                      </Text>
                    )}
                    <Tag style={{ fontSize: 11 }}>
                      {item.querySource === "natural_language" ? "NL" : "SQL"}
                    </Tag>
                  </Space>
                }
                description={
                  <Text
                    code
                    style={{
                      fontSize: 12,
                      display: "block",
                      maxWidth: 640,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.sqlText}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </Card>
  );
};
