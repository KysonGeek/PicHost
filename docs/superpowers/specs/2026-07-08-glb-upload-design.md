# PicHost 上传支持 .glb 设计

日期:2026-07-08
状态:已确认
前置:文件夹功能(2026-07-08-folders-design.md)已合并——上传响应含 folder_id,glb 与图片一样参与文件夹分类。

## 目标

允许上传 .glb(binary glTF 2.0,3D 模型)文件:存储 + 外链分享 + 画廊图标卡片。不做浏览器内 3D 渲染预览。

## 关键决策

- **仅存储和外链**:不引入 model-viewer 等 3D 渲染组件。画廊/上传结果中 glb 显示内置 3D 图标占位,点击展示链接面板而非灯箱大图。
- **大小限制按类型**:图片维持 20MB(`MAX_SIZE`),glb 单独 `MAX_GLB_SIZE = 100 * 1024 * 1024`(100MB)。部署注意:前置反代(Caddy/Nginx)的 request body 上限需同步 ≥100MB,README 部署段注明。
- **复制链接仅直链**:glb 的链接面板只有「直链」一行;图片保持 直链/BBCode/Markdown/HTML 四种不变。
- **最小增量**:不建通用附件系统。glb 走既有 images 表、既有 uploads/ 平铺目录、既有文件夹/筛选/删除逻辑。

## 后端

### 放行规则(初筛)

浏览器对 .glb 常上报 `model/gltf-binary`、`application/octet-stream` 或空 content_type。上传初筛改为:

- content_type ∈ ALLOWED_MIME(新增 `model/gltf-binary`),**或**
- 原始文件名(小写)以 `.glb` 结尾且 content_type ∈ {`application/octet-stream`, ``, None}

其余仍 415。最终裁决靠字节校验。

### 大小检查

读取 content 后:目标类型为 glb → 上限 100MB(超出 413「文件大小超过 100MB 限制」);其余 → 现行 20MB。

### 字节校验(_process_upload 新分支,与 SVG 分支并列)

GLB 文件头(12 字节,小端):`magic == b"glTF"`(offset 0-3)、`version == 2`(offset 4-7 uint32)。不符 → 415「文件内容不是有效的 GLB」,不落盘。通过 → 写 `{file_id}.glb`,**不生成缩略图**(thumbs/ 无对应文件),返回 `{filename, width: None, height: None, mime: "model/gltf-binary"}`。

### 其他

- `_mime_to_ext` 加 `"model/gltf-binary": ".glb"`。
- DB 无 schema 变化(width/height 本就可空)。
- `/uploads/` 现有 CSP sandbox + immutable cache 响应头对 glb 同样适用。

## 前端

- `ALLOWED_UPLOAD_TYPES` 加 `model/gltf-binary`;`uploadSingle` 的类型预检放行「文件名以 .glb 结尾」的文件(不看 type),并按 .glb 用 100MB 上限(`MAX_GLB_UPLOAD_SIZE`,提示文案「超过 100MB 限制」)。
- `<input accept>` 改为 `image/*,.glb`;拖拽过滤(目前 `type.startsWith('image/')`)与粘贴过滤放行 .glb 后缀。
- 判定函数 `isGlb(item)`:`mime_type === 'model/gltf-binary'`(列表数据)或文件名 .glb 结尾(File 对象)。
- 画廊卡片与上传结果预览:glb 显示内置 3D 立方体 SVG 图标(灰底占位,不发缩略图请求);卡片点击时 glb 仍打开灯箱,但灯箱主区不加载 `<img>`,显示同款 3D 图标占位,下方链接面板照常(仅直链一行)。
- `buildLinks(url, name, isGlbFlag)`:isGlbFlag 为真时仅返回「直链」一行。
- 上传区文案:「支持 JPG · PNG · GIF · WebP · BMP · SVG · GLB | 图片最大 20MB · 模型最大 100MB」。
- glb 参与文件夹:上传下拉框、筛选、移动、删除逻辑零改动(全走 images 表既有路径)。

## 错误处理

- 415(类型/字节不合法)、413(超限)沿用现有 toast 展示后端 detail 的机制。
- 无缩略图不是错误:前端对 glb 不请求 thumbs,不触发 onerror 回退链。

## 测试

新增 `tests/test_glb.py`(复用 conftest 与 test_folders 的 helper 风格):

- `make_glb` fixture:构造最小合法 GLB(12 字节头 + 空 JSON chunk)。
- 合法 glb 以 `model/gltf-binary` 上传成功:响应 mime/width=None/height=None 正确、uploads/ 有文件、thumbs/ 无文件。
- 以 `application/octet-stream` + .glb 文件名上传成功(浏览器常见情形)。
- 伪造字节(非 glTF 魔数 / version≠2)→ 415,且不落盘。
- .glb 后缀但 content_type 为 image/png 之类 → 走图片校验(Pillow 拒绝)。
- 超 100MB glb → 413;>20MB 且 <100MB 的 glb 成功,同大小 PNG → 413(验证按类型限额)。
- glb 上传进文件夹 + 按文件夹筛选 + 移动 + 删除全链路(复用 folder helpers)。
