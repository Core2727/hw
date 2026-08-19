# FEATURE_EXPORT.md — 数据导出功能交付说明

> 作业：《为"数据库查询工具"添加数据导出功能》
> 原项目：Cursor 构建的"智能数据库查询工具"（FastAPI + React/antd + Monaco）
> 本次开发工具：**Claude Code**（Agent + 自定义 Command）

---

## 1. 作业要求对照表

| 作业要求 | 实现情况 |
|---|---|
| **导出格式：至少两种（CSV、JSON）** | ✅ 支持 CSV 和 JSON。CSV 带 UTF-8 BOM（Excel 打开中文不乱码）、标准引号转义、NULL→空串；JSON 为 UTF-8 缩进格式，保留 Unicode 原文 |
| **自动化：Agent 或自定义 Command，查询+导出一键完成** | ✅ 新增 Claude Code 自定义 slash 命令 `/export-query`（`.claude/commands/export-query.md`），一句话完成 NL→SQL→查询→导出全流程 |
| **用户交互：自然语言或界面操作触发导出** | ✅ 三种交互方式：① 查询成功后 AI 主动弹窗询问"是否导出为 CSV/JSON"；② 结果区常驻 EXPORT 按钮；③ 查询历史每条记录可一键重导出 |

---

## 2. 功能总览

在原有"智能数据库查询工具"上新增**服务端导出能力**，形成完整闭环：

```
┌─────────────────────────── 用户交互层 ───────────────────────────┐
│                                                                  │
│  ① 查询成功 → 右下角通知主动询问 [EXPORT CSV] [EXPORT JSON]      │
│  ② RESULTS 卡片常驻 EXPORT 按钮                                  │
│  ③ QUERY HISTORY 卡片：每条历史 SQL 旁 [CSV] [JSON] 重导出       │
│  ④ 终端: /export-query <db> <自然语言或SQL> [csv|json]           │
│     (Claude Code 自定义 Command)                                 │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTP
                               ▼
┌─────────────────────────── 后端 API 层 ──────────────────────────┐
│                                                                  │
│  POST /api/v1/dbs/{name}/query/export   执行查询并导出           │
│  GET  /api/v1/dbs/{name}/exports        列出历史导出文件         │
│  GET  /api/v1/dbs/{name}/exports/{file} 下载已导出文件           │
│                                                                  │
│  api/v1/exports.py ──> query_wrapper.execute_query_with_service │
│                         (复用现有 SQL 校验/LIMIT/适配器链路)      │
│                       ──> services/export_service.py             │
│                         (序列化 CSV/JSON + 落盘 + 元数据)         │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
              ~/.db_query/exports/<db>/<db>_<时间戳>.csv
              ~/.db_query/exports/<db>/<db>_<时间戳>.csv.meta.json
              (元数据: SQL、行数、导出时间、格式)
```

### 关键设计决策

1. **复用现有查询链路**：导出端点调用与正常查询完全相同的
   `execute_query_with_service()`，因此自动继承 SQL 安全校验
   （仅允许 SELECT）和自动 LIMIT 注入（默认 1000 行），零逻辑重复。
2. **服务端落盘 + 浏览器下载双通道**：文件先写入服务端
   `~/.db_query/exports/<数据库名>/` 目录（附 `.meta.json` 元数据），
   同时通过下载端点推送到浏览器，两边都能拿到文件。
3. **每个导出文件自带元数据 sidecar**：`<文件名>.meta.json` 记录
   生成该文件的 SQL、行数、时间戳，导出物可追溯、可复现。

---

## 3. API 说明

### 3.1 执行查询并导出

```
POST /api/v1/dbs/{name}/query/export
Content-Type: application/json

{
  "sql": "SELECT id, name FROM users",   // 必填，仅 SELECT
  "format": "csv"                         // 可选，"csv"(默认) | "json"
}
```

响应（200）：

```json
{
  "filename": "test_db_20260819T153000.csv",
  "format": "csv",
  "databaseName": "test_db",
  "sql": "SELECT id, name FROM users",
  "rowCount": 42,
  "filePath": "~/.db_query/exports/test_db/test_db_20260819T153000.csv",
  "fileSizeBytes": 10240,
  "exportedAt": "2026-08-19T15:30:00",
  "downloadUrl": "/api/v1/dbs/test_db/exports/test_db_20260819T153000.csv"
}
```

错误：404 数据库连接不存在 / 400 SQL 校验失败（非 SELECT 等） /
422 格式不合法 / 500 查询执行失败。

### 3.2 列出历史导出

```
GET /api/v1/dbs/{name}/exports
```

返回该数据库的所有导出文件（含 sidecar 元数据），按导出时间倒序。

### 3.3 下载导出文件

```
GET /api/v1/dbs/{name}/exports/{filename}
```

以 `text/csv` 或 `application/json` 流式返回文件内容。
文件名经过安全校验，路径穿越（`../`）会被拒绝。

---

## 4. 前端交互说明

### 4.1 查询成功后主动询问（作业要求的 AI 助手主动交互）

每次查询成功返回 ≥1 行时，右下角弹出 antd notification：

> **Export this query result?**
> Query returned 42 rows. Export this result to a file?
> [EXPORT CSV] [EXPORT JSON] [×]
> Files are saved on the server (with SQL metadata) and downloaded to your browser.

- 点击按钮 → 走后端导出端点 → 浏览器自动下载
- 点 × 或执行下一次查询时自动关闭
- 实现在 `frontend/src/components/ExportPrompt.tsx` + `Home.tsx` 的 `offerExport()`

### 4.2 结果区常驻导出按钮

RESULTS 卡片右上角原有的 EXPORT CSV / EXPORT JSON 按钮保留，
但行为从"前端拼文件"改为调用后端导出端点（数据由服务端重新执行
SQL 生成，保证与导出时点的数据库状态一致）。

### 4.3 查询历史重导出

页面底部新增 QUERY HISTORY 卡片（`QueryHistoryPanel.tsx`）：

- 展示最近 50 条查询：成功/失败状态、执行时间、行数、SQL 摘要、来源（SQL/NL）
- 每条成功记录旁有三个操作：
  - **RE-RUN**：把该 SQL 载入编辑器（不自动执行）
  - **CSV / JSON**：重新执行该 SQL 并导出对应格式
- 查询或导出后自动刷新

---

## 5. Claude Code 自定义 Command

命令文件：`.claude/commands/export-query.md`（仓库根目录）

在项目根目录下打开 Claude Code，输入：

```
/export-query test_db 查询最近7天下过订单的用户 csv
```

命令会驱动 Claude 按以下流程执行（详见命令文件内的分步指令）：

1. 解析参数：数据库连接名、查询意图、格式（默认 csv）
2. 输入是自然语言 → 调 `/query/natural`（NL2SQL）生成 SQL 并展示解释
3. 输入是 SQL → 直接使用
4. 调 `POST /query/export` 一键完成"查询 + 导出落盘"
5. 向用户报告文件路径、行数、大小和下载方式
6. 参数不全时自动列出可用数据库连接并询问

这一步实现了作业要求的"执行查询 + 导出结果一键完成"。

---

## 6. 代码改动清单

### 后端（`backend/`）

| 文件 | 改动 |
|---|---|
| `app/services/export_service.py` | **新增** ExportService：CSV/JSON 序列化、文件落盘、sidecar 元数据、列表/下载辅助、文件名安全处理 |
| `app/api/v1/exports.py` | **新增** 导出路由（POST 导出 / GET 列表 / GET 下载） |
| `app/models/schemas.py` | **新增** ExportRequest / ExportResponse / ExportFileInfo 三个 schema |
| `app/main.py` | 注册 exports router（3 行） |
| `tests/unit/test_export.py` | **新增** 38 个单元测试 |

### 前端（`frontend/src/`）

| 文件 | 改动 |
|---|---|
| `services/exportApi.ts` | **新增** exportQuery / downloadExport / listExports API 封装 |
| `components/ExportPrompt.tsx` | **新增** 查询后主动询问的导出提示卡片 |
| `components/QueryHistoryPanel.tsx` | **新增** 查询历史面板（RE-RUN / CSV / JSON） |
| `pages/Home.tsx` | 接入主动询问通知；EXPORT 按钮改走后端；挂载历史面板 |

### Claude Code

| 文件 | 改动 |
|---|---|
| `.claude/commands/export-query.md` | **新增** /export-query 自定义命令 |

---

## 7. 测试与验证

### 后端单元测试（38 个，全部通过）

```
cd backend
OPENAI_API_KEY=dummy .venv/bin/python -m pytest tests/unit/test_export.py -v
```

覆盖：

- CSV 序列化：表头/行数、BOM 前缀、逗号/引号/换行转义（csv.reader round-trip）、
  NULL→空串、中文不乱码
- JSON 序列化：结构（columns+rowCount+rows）、保留 null、中文不转义成 \uXXXX
- 未知格式抛 ValueError
- 文件落盘：文件+sidecar 均生成、目录自动创建、不安全数据库名被净化
- 列表：按库过滤、跳过 sidecar 文件、元数据合并
- 下载路径解析：拒绝路径穿越、不存在文件返回 None
- API 端点：CSV/JSON 导出成功、默认格式、404/422/400/500 错误路径、
  导出→下载 round-trip、空结果仍写文件

> 注：本机验证使用项目内 `.venv`（Python 3.10；项目 pyproject 声明 >=3.12，
> 但全部相关代码 3.10 兼容，测试通过）。原有测试套件中另有 20 个
> **历史遗留失败**（patch 目标与重构后代码不一致所致），与本次改动无关，
> 本次未触碰。

### 前端语法检查

改动/新增的 4 个 TS/TSX 文件均通过 esbuild 解析检查（--loader=tsx）。

### 建议的手工验收路径（需要真实数据库）

```
cd w2/db_query && make dev
# 1) 浏览器 http://localhost:5173 → 选库 → 执行查询 → 右下角应弹导出询问
# 2) 点 EXPORT CSV → 验证下载的文件 + ~/.db_query/exports/<db>/ 下的落盘文件
# 3) QUERY HISTORY 卡片 → 对历史 SQL 点 CSV/JSON 重导出
# 4) 终端(项目根): claude 里执行 /export-query <db> <问题> csv
```

---

## 8. 已知限制

1. **导出即重执行**：导出端点会重新执行 SQL（而不是缓存上次结果），
   因此导出文件反映导出时点的数据库状态，可能与屏幕上片刻前看到的结果
   有微小差异（数据持续变化时）。这是服务端导出的通用取舍，换来的是
   Command/历史重导出等场景无需依赖前端状态。
2. **行数上限**：与页面查询一致，SQL 未写 LIMIT 时自动注入 LIMIT 1000
   （复用现有 `query_default_limit` 配置）。
3. **文件按"分钟级"时间戳命名**：同一分钟内多次导出会生成多个不同文件
   （时间戳相同则文件名后缀递增），不做去重合并。
