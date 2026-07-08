# PicHost 文件夹功能设计

日期:2026-07-08
状态:已确认

## 目标

为图床增加文件夹(单层、逻辑分组)能力:上传时选择文件夹、画廊按文件夹筛选、图片可在文件夹间移动、文件夹可新建/重命名/删除。

## 关键决策

- **仅逻辑分组**:文件夹只存在于数据库中,图片 URL 保持 `/uploads/{filename}` 不变。移动图片、重命名/删除文件夹都不影响已分享的外链,也不涉及文件系统路径安全问题。
- **单层结构**:不支持嵌套子文件夹。
- **删除文件夹是非破坏性的**:其中图片回到「未分类」,不删除图片。

## 数据模型

```sql
CREATE TABLE IF NOT EXISTS folders (
    id         TEXT PRIMARY KEY,   -- uuid4 hex 前 12 位,与图片 id 风格一致
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

ALTER TABLE images ADD COLUMN folder_id TEXT;  -- NULL = 未分类
CREATE INDEX IF NOT EXISTS idx_images_folder_id ON images(folder_id);
```

- 迁移在 `init_db()` 中幂等执行:通过 `PRAGMA table_info(images)` 检查 `folder_id` 列是否存在,不存在才 `ALTER TABLE`。老库启动后所有图片自动为未分类。
- 不使用外键约束(SQLite 默认关闭),由应用逻辑保证一致性:删除文件夹时,先在同一事务中把其图片的 `folder_id` 置 NULL,再删 folders 行。

## API(全部依赖现有 `require_auth`)

| 接口 | 请求 | 响应/行为 |
|---|---|---|
| `GET /api/folders` | — | `{folders: [{id, name, created_at, count}], uncategorized: n}`;count 为该文件夹图片数 |
| `POST /api/folders` | `{name}` | 201 创建;重名 → 409 |
| `PATCH /api/folders/{id}` | `{name}` | 重命名;不存在 → 404,重名 → 409 |
| `DELETE /api/folders/{id}` | — | 事务内:图片 folder_id 置 NULL,删除文件夹;不存在 → 404 |
| `PATCH /api/images/{id}` | `{folder_id}`(可为 null) | 移动图片;图片或目标文件夹不存在 → 404 |
| `POST /api/upload` | 新增可选 query `folder_id` | 指定的文件夹不存在 → 404;上传结果包含 folder_id |
| `GET /api/images` | 新增可选 query `folder` | 不传 = 全部;`folder=<id>` = 该文件夹;`folder=none` = 仅未分类。total 同步按筛选条件统计 |

**文件夹名规则**:去首尾空白后非空、长度 ≤ 50。不做字符白名单(纯逻辑名称,前端输出统一走既有 escHtml/escAttr 转义)。校验用 Pydantic,非法 → 422。

## 前端 UI

- **上传区**:上传卡片内加「上传到文件夹」下拉框(默认「未分类」,列出所有文件夹),`uploadSingle` 的请求 URL 附带所选 `folder_id`。
- **画廊筛选**:标题下方一排文件夹 chips:「全部」「未分类」+ 各文件夹(带数量),点击切换筛选并重置分页从第 1 页加载;末尾「+ 新建文件夹」按钮(prompt 输入名称)。
- **文件夹管理**:选中某个具体文件夹 chip 时,显示「重命名」(prompt)与「删除」(confirm,文案说明图片将回到未分类)按钮。
- **移动图片**:画廊 hover 遮罩上「删除」旁增加「移动」按钮,弹出文件夹选择(含「未分类」);移动后若图片不再匹配当前筛选,则从视图移除并修正计数。
- **上传插入逻辑**:上传成功后,仅当新图匹配当前筛选(全部/未分类/对应文件夹)时才 prepend 到画廊;计数与文件夹 chips 数量同步刷新。

## 错误处理

- 后端:409(重名)、404(不存在)、422(名称非法);删除文件夹的置 NULL + 删行在同一事务。
- 前端:409/404 的 `detail` 用现有 toast 展示;401 走既有 `handleUnauth`。

## 测试

新增 `tests/test_folders.py`,复用现有 conftest 隔离 fixture:

- 文件夹 CRUD:创建、列表带计数、重命名、删除;重名创建/重命名 → 409;操作不存在的文件夹 → 404。
- 上传到文件夹:合法 folder_id 成功且入库正确;不存在的 folder_id → 404。
- 筛选:`folder=<id>`、`folder=none`、不传三种情况的列表与 total。
- 移动:移入文件夹、移回未分类(null);移动到不存在的文件夹 → 404。
- 删除文件夹后其图片回到未分类。
- 迁移:对无 folder_id 列的旧库结构执行 init_db 能正常升级并工作。
