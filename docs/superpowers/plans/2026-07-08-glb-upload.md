# .glb 上传支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 允许上传 .glb(binary glTF 2.0)文件:魔数校验、单独 100MB 上限、画廊 3D 图标卡片、仅直链复制;不做浏览器内 3D 渲染。

**Architecture:** 最小增量:glb 走既有 images 表、uploads/ 平铺目录与文件夹/筛选/删除逻辑。后端在 `_process_upload` 加一个与 SVG 并列的 glb 分支(12 字节头校验,不生成缩略图);前端按 `mime_type === 'model/gltf-binary'` 切换图标占位与仅直链的链接面板。

**Tech Stack:** FastAPI + aiosqlite(后端),原生 JS/CSS(前端),pytest + TestClient(测试)。

**Spec:** `docs/superpowers/specs/2026-07-08-glb-upload-design.md`

## Global Constraints

- 图片上限维持 `MAX_SIZE = 20MB` 不变;glb 单独 `MAX_GLB_SIZE = 100 * 1024 * 1024`。
- GLB 字节校验:前 12 字节,`content[0:4] == b"glTF"` 且 `int.from_bytes(content[4:8], "little") == 2`;不符 → 415「文件内容不是有效的 GLB」,不落盘。
- glb 不生成缩略图(thumbs/ 无对应文件);width/height 存 NULL;mime 存 `model/gltf-binary`。
- 放行初筛:content_type ∈ ALLOWED_MIME(新增 `model/gltf-binary`),或文件名(小写)以 `.glb` 结尾且 content_type ∈ {`application/octet-stream`, 空, None}。
- 前端:glb 的复制链接面板仅「直链」一行;图片四种格式不变。glb 卡片/灯箱显示内置 3D 立方体 SVG 图标,不请求缩略图。
- 中文错误文案与现有风格一致;所有新逻辑走既有 require_auth(上传端点已有)。
- 测试命令用 `python3 -m pytest`(本机无 `python` 别名)。每个 Task 结束全绿再提交。

---

### Task 1: 后端 —— glb 放行、按类型限额、字节校验与存储

**Files:**
- Modify: `main.py:31-39`(常量区)
- Modify: `main.py:269-295`(upload 端点初筛与大小检查)
- Modify: `main.py:419-424`(`_mime_to_ext`)
- Modify: `main.py:452+`(`_process_upload` 加 glb 分支,签名加参数)
- Test: `tests/test_glb.py`(新建)

**Interfaces:**
- Produces: 上传接口接受 glb;成功响应 `mime_type == "model/gltf-binary"`、`width/height == null`;`MAX_GLB_SIZE` 模块常量(Task 2 前端提示与之对齐);`_process_upload(content, is_svg, is_glb, file_id)` 新签名。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_glb.py`:

```python
"""GLB (binary glTF 2.0) upload support: acceptance, validation, size caps."""
import struct
import sqlite3

import pytest


@pytest.fixture
def make_glb():
    """Minimal valid GLB: 12-byte header + one JSON chunk, padded to `size`."""
    def _make(size=None, magic=b"glTF", version=2):
        json_payload = b'{"asset":{"version":"2.0"}}'
        pad = (4 - len(json_payload) % 4) % 4
        json_payload += b" " * pad
        if size is not None:
            extra = size - (12 + 8 + len(json_payload))
            if extra > 0:
                json_payload += b" " * extra
        chunk = struct.pack("<II", len(json_payload), 0x4E4F534A) + json_payload
        total = 12 + len(chunk)
        header = magic + struct.pack("<II", version, total)
        return header + chunk
    return _make


def _upload_glb(client, auth, body, content_type="model/gltf-binary",
                name="model.glb", folder_id=None):
    url = "/api/upload" + (f"?folder_id={folder_id}" if folder_id else "")
    return client.post(url, headers=auth, files={"file": (name, body, content_type)})


def test_glb_upload_roundtrip(client, auth, make_glb, mainmod):
    r = _upload_glb(client, auth, make_glb())
    assert r.status_code == 200
    data = r.json()
    assert data["mime_type"] == "model/gltf-binary"
    assert data["width"] is None and data["height"] is None
    assert data["filename"].endswith(".glb")
    assert (mainmod.UPLOAD_DIR / data["filename"]).exists()
    assert not (mainmod.THUMB_DIR / data["filename"]).exists()  # no thumbnail


def test_glb_upload_as_octet_stream(client, auth, make_glb):
    """Browsers often report .glb as application/octet-stream (or nothing)."""
    r = _upload_glb(client, auth, make_glb(), content_type="application/octet-stream")
    assert r.status_code == 200
    assert r.json()["mime_type"] == "model/gltf-binary"


def test_octet_stream_without_glb_name_rejected(client, auth, make_glb):
    r = _upload_glb(client, auth, make_glb(), content_type="application/octet-stream",
                    name="model.bin")
    assert r.status_code == 415


@pytest.mark.parametrize("kwargs", [{"magic": b"FAKE"}, {"version": 1}])
def test_invalid_glb_bytes_rejected(client, auth, make_glb, mainmod, kwargs):
    r = _upload_glb(client, auth, make_glb(**kwargs))
    assert r.status_code == 415
    conn = sqlite3.connect(mainmod.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    conn.close()
    assert not any(p.is_file() for p in mainmod.UPLOAD_DIR.glob("*.glb"))


def test_glb_name_with_image_type_goes_image_path(client, auth, make_glb):
    """A .glb named file claiming image/png must pass Pillow — and fail."""
    r = _upload_glb(client, auth, make_glb(), content_type="image/png")
    assert r.status_code == 415


def test_glb_over_glb_cap_rejected(client, auth, make_glb, mainmod, monkeypatch):
    monkeypatch.setattr(mainmod, "MAX_GLB_SIZE", 1024)
    r = _upload_glb(client, auth, make_glb(size=2048))
    assert r.status_code == 413
    # detail wording is type-based ("100MB" for glb), independent of the patched cap
    assert "100MB" in r.json()["detail"]


def test_glb_between_image_and_glb_cap_allowed(client, auth, make_glb):
    """>20MB is fine for glb (its cap is 100MB), while images stay capped."""
    r = _upload_glb(client, auth, make_glb(size=21 * 1024 * 1024))
    assert r.status_code == 200


def test_image_cap_unchanged(client, auth):
    blob = b"\x89PNG" + b"\x00" * (21 * 1024 * 1024)
    r = client.post("/api/upload", headers=auth,
                    files={"file": ("big.png", blob, "image/png")})
    assert r.status_code == 413
    assert "20MB" in r.json()["detail"]


def test_glb_participates_in_folders(client, auth, make_glb):
    fid = client.post("/api/folders", headers=auth, json={"name": "模型"}).json()["id"]
    up = _upload_glb(client, auth, make_glb(), folder_id=fid)
    assert up.status_code == 200 and up.json()["folder_id"] == fid

    listed = client.get(f"/api/images?folder={fid}", headers=auth).json()
    assert listed["total"] == 1
    assert listed["images"][0]["mime_type"] == "model/gltf-binary"

    img_id = up.json()["id"]
    assert client.patch(f"/api/images/{img_id}", headers=auth,
                        json={"folder_id": None}).status_code == 200
    assert client.delete(f"/api/images/{img_id}", headers=auth).status_code == 200
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_glb.py -v`
Expected: 大部分 FAIL(415「不支持的文件类型」——初筛还不认识 glb);`test_image_cap_unchanged` 应已 PASS。

- [ ] **Step 3: 实现**

`main.py` 常量区:`ALLOWED_MIME` 加一项,并在 `MAX_SIZE` 下新增:

```python
ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/svg+xml",
    "model/gltf-binary",
}
```

```python
MAX_GLB_SIZE = 100 * 1024 * 1024  # 100 MB cap for .glb models (images stay at MAX_SIZE)
```

`ALLOWED_EXT` 加 `".glb"`。

upload 端点(main.py:270 起)初筛与大小检查改为:

```python
async def upload(file: UploadFile = File(...), folder_id: str | None = Query(None)):
    name_lower = (file.filename or "").lower()
    # Browsers commonly report .glb as application/octet-stream or nothing at
    # all, so fall back to the extension for those; real validation is by bytes.
    is_glb = file.content_type == "model/gltf-binary" or (
        name_lower.endswith(".glb")
        and file.content_type in (None, "", "application/octet-stream")
    )
    if not is_glb and file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {file.content_type}")
```

(原 `if file.content_type not in ALLOWED_MIME` 两行被以上替换;`model/gltf-binary` 进 ALLOWED_MIME 后,该 content_type 也会把 is_glb 判真——注意 is_glb 的第一个条件已覆盖。)

大小检查(main.py:285-286)改为:

```python
    size_cap = MAX_GLB_SIZE if is_glb else MAX_SIZE
    if len(content) > size_cap:
        limit_label = "100MB" if is_glb else "20MB"
        raise HTTPException(status_code=413, detail=f"文件大小超过 {limit_label} 限制")
```

`is_svg` 行(main.py:289)后追加 glb 传参,线程池调用改签名:

```python
    is_svg = file.content_type == "image/svg+xml"

    # Validate the actual bytes and write original + thumbnail, off the event loop.
    try:
        meta = await run_in_threadpool(_process_upload, content, is_svg, is_glb, file_id)
```

`_mime_to_ext`(main.py:419)映射加 `"model/gltf-binary": ".glb",`。

`_process_upload` 签名与 glb 分支(放在 is_svg 分支之前):

```python
def _process_upload(content: bytes, is_svg: bool, is_glb: bool, file_id: str) -> dict:
    """Validate bytes, then write the original (+ thumbnail for images).

    Runs in a threadpool. Files are written ONLY after validation succeeds, so
    rejected uploads never persist. Raises _UploadRejected on anything that
    isn't a supported file. Returns {filename, width, height, mime}.
    """
    if is_glb:
        # GLB header: magic "glTF" + uint32 version (little-endian) == 2.
        if len(content) < 12 or content[0:4] != b"glTF" \
                or int.from_bytes(content[4:8], "little") != 2:
            raise _UploadRejected("文件内容不是有效的 GLB")
        filename = f"{file_id}.glb"
        (UPLOAD_DIR / filename).write_bytes(content)  # no thumbnail for models
        return {"filename": filename, "width": None, "height": None,
                "mime": "model/gltf-binary"}

    if is_svg:
        ...  # 原分支不动
```

- [ ] **Step 4: 运行全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全部 PASS(42 旧 + 9 新)

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_glb.py
git commit -m "feat(api): accept .glb uploads (magic-byte validation, 100MB cap, no thumbnail)"
```

---

### Task 2: 前端 —— 接受 .glb(选择/拖拽/粘贴、限额、文案)

**Files:**
- Modify: `static/index.html:36`(accept)与 46 行附近的提示文案
- Modify: `static/app.js:39-42`(常量)、`static/app.js:120-145`(拖拽/粘贴过滤)、`static/app.js:168-176`(uploadSingle 预检)

**Interfaces:**
- Consumes: Task 1 的后端接受规则(octet-stream + .glb 文件名可上传)。
- Produces: `isGlbFile(file)`(File 对象判定,Task 3 无依赖但共用命名风格);`MAX_GLB_UPLOAD_SIZE` 常量。

- [ ] **Step 1: index.html**

36 行 `accept="image/*"` 改为 `accept="image/*,.glb"`;46 行提示改为:

```html
          <p class="upload-hint-sub">支持 JPG · PNG · GIF · WebP · BMP · SVG · GLB &nbsp;|&nbsp; 图片最大 20MB · 模型最大 100MB</p>
```

- [ ] **Step 2: app.js 常量与判定**

`MAX_UPLOAD_SIZE`(39 行)下方加:

```js
const MAX_GLB_UPLOAD_SIZE = 100 * 1024 * 1024;  // keep in sync with backend MAX_GLB_SIZE
```

`ALLOWED_UPLOAD_TYPES` 字面量加 `'model/gltf-binary',`。其下加:

```js
function isGlbFile(file) { return (file.name || '').toLowerCase().endsWith('.glb'); }
```

- [ ] **Step 3: 过滤器与预检**

拖拽过滤(125 行)改为:

```js
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/') || isGlbFile(f));
```

粘贴过滤(136-140 行)改为(先取 File 再统一过滤,可按文件名识别 glb):

```js
  const files = [...items]
    .filter(i => i.kind === 'file')
    .map(i => i.getAsFile())
    .filter(Boolean)
    .filter(f => f.type.startsWith('image/') || isGlbFile(f));
```

`uploadSingle` 预检(168-176 行)改为:

```js
function uploadSingle(file) {
  const glb = isGlbFile(file) || file.type === 'model/gltf-binary';
  const sizeCap = glb ? MAX_GLB_UPLOAD_SIZE : MAX_UPLOAD_SIZE;
  if (file.size > sizeCap) {
    showToast(`${file.name || '文件'} 超过 ${glb ? '100MB' : '20MB'} 限制`, 'error');
    return;
  }
  if (!glb && file.type && !ALLOWED_UPLOAD_TYPES.has(file.type)) {
    showToast(`不支持的文件类型：${file.type}`, 'error');
    return;
  }
```

(glb 文件浏览器常给 `application/octet-stream` 或空 type,故 glb 不做 type 白名单预检,交给后端字节校验。)

- [ ] **Step 4: 验证 + Commit**

Run: `python3 -m pytest tests/ -q`(后端未动,应全绿)
Run: `node --check static/app.js`
Expected: 均通过

```bash
git add static/index.html static/app.js
git commit -m "feat(web): accept .glb in picker, drag-drop and paste with 100MB hint"
```

---

### Task 3: 前端 —— glb 图标展示、仅直链、README

**Files:**
- Modify: `static/app.js:244-293`(buildLinks / prependResult)、`static/app.js:447-472`(buildGalleryItem)、`static/app.js:562-591`(openLightbox/closeLightbox)
- Modify: `static/index.html:89` 附近(lightbox 内加 glb 占位节点)
- Modify: `static/style.css`(文件末尾加占位样式)
- Modify: `README.md:9,15`(功能/格式)、`README.md:69-81` Caddy/安全段、API 表 105 行说明

**Interfaces:**
- Consumes: 列表/上传响应的 `mime_type` 字段;Task 2 的常量(无直接调用)。
- Produces: `isGlbData(data)`、`GLB_ICON` 常量、`buildLinks(url, name, glb)` 第三参数、`openLightbox(url, name, glb)` 第三参数。

- [ ] **Step 1: app.js 判定与图标常量**

`buildLinks` 上方(244 行前)加:

```js
function isGlbData(data) { return data.mime_type === 'model/gltf-binary'; }

const GLB_ICON = `
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48" aria-hidden="true">
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
    <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
    <line x1="12" y1="22.08" x2="12" y2="12"/>
  </svg>`;
```

- [ ] **Step 2: buildLinks 仅直链**

```js
function buildLinks(url, name, glb = false) {
  if (glb) return [{ label: '直链', value: url }];
  const n = name || '';
  return [
    { label: '直链',      value: url },
    { label: 'BBCode',   value: `[img]${url}[/img]` },
    { label: 'Markdown', value: `![${escMarkdown(n)}](${url})` },
    { label: 'HTML',     value: `<img src="${url}" alt="${escHtml(n)}">` },
  ];
}
```

- [ ] **Step 3: prependResult 与 buildGalleryItem 的预览分支**

`prependResult` 中 `const links = ...` 与 `<div class="result-preview">` 部分改为:

```js
  const glb   = isGlbData(data);
  const links = buildLinks(url, data.orig_name || data.filename, glb);
  const preview = glb
    ? `<div class="glb-placeholder">${GLB_ICON}</div>`
    : `<img src="/uploads/thumbs/${data.filename}"
            onerror="this.src='/uploads/${data.filename}'"
            alt="${escHtml(data.orig_name || data.filename)}" />`;
```

模板里 `<div class="result-preview">` 的内容替换为 `${preview}`。

`buildGalleryItem` 同样:

```js
function buildGalleryItem(data) {
  const url  = `${window.location.origin}/uploads/${data.filename}`;
  const glb  = isGlbData(data);
  const item = document.createElement('div');
  item.className = 'gallery-item';
  item.dataset.id = data.id;
  const preview = glb
    ? `<div class="glb-placeholder">${GLB_ICON}</div>`
    : `<img src="/uploads/thumbs/${data.filename}"
            onerror="this.src='/uploads/${data.filename}'"
            alt="${escHtml(data.orig_name || data.filename)}" loading="lazy" />`;
  item.innerHTML = `
    ${preview}
    <div class="gallery-overlay">
      <span class="gallery-item-name">${escHtml(data.orig_name || data.filename)}</span>
      <button class="btn-move" data-id="${escAttr(data.id)}">移动</button>
      <button class="btn-delete" data-id="${escAttr(data.id)}">删除</button>
    </div>`;

  item.addEventListener('click', () => openLightbox(url, data.orig_name || data.filename, glb));
  item.querySelector('.btn-move').addEventListener('click', e => {
    e.stopPropagation();
    openMoveModal(data, item);
  });
  item.querySelector('.btn-delete').addEventListener('click', e => {
    e.stopPropagation();
    confirmDelete(data.id, item);
  });
  return item;
}
```

- [ ] **Step 4: 灯箱 glb 占位**

`static/index.html` 的 `<img class="lightbox-img" id="lightboxImg" ...>` 之后加:

```html
      <div class="lightbox-glb" id="lightboxGlb" style="display:none"></div>
```

`openLightbox`/`closeLightbox` 改为:

```js
const lightboxGlb = document.getElementById('lightboxGlb');

function openLightbox(url, name, glb = false) {
  if (glb) {
    lightboxImg.style.display = 'none';
    lightboxGlb.innerHTML = GLB_ICON + `<p>${escHtml(name || '')}</p>`;
    lightboxGlb.style.display = 'flex';
  } else {
    lightboxGlb.style.display = 'none';
    lightboxImg.style.display = '';
    lightboxImg.src = url;
  }
  lightbox.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  const links = buildLinks(url, name, glb);

  const linksEl = document.getElementById('lightboxLinks');
  linksEl.innerHTML = links.map(l => `
    <div class="link-row">
      <span class="link-label">${escHtml(l.label)}</span>
      <span class="link-input" title="${escAttr(l.value)}">${escHtml(l.value)}</span>
      <button class="btn-copy" data-value="${escAttr(l.value)}">复制</button>
    </div>`).join('');

  linksEl.querySelectorAll('.btn-copy').forEach(btn => {
    btn.addEventListener('click', () => copyText(btn.dataset.value, btn));
  });
}

function closeLightbox() {
  lightbox.style.display = 'none';
  lightboxImg.src = '';
  lightboxGlb.innerHTML = '';
  document.body.style.overflow = '';
}
```

(`const lightboxGlb` 放到文件顶部 Globals 区,与其他 getElementById 并列。)

- [ ] **Step 5: style.css**

文件末尾追加:

```css
/* ── GLB placeholder ─────────────────────────────────────────────────────── */
.glb-placeholder {
  width: 100%; height: 100%; min-height: 90px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface2); color: var(--primary-light);
}
.gallery-item .glb-placeholder { aspect-ratio: 1; }
.lightbox-glb {
  flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; padding: 60px 80px; background: var(--surface2);
  border-radius: var(--radius); color: var(--primary-light);
}
.lightbox-glb p { color: var(--text-muted); font-size: .9rem; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 6: README**

- 9 行改为:`- 上传后自动生成直链、BBCode、Markdown、HTML 四种格式，一键复制（.glb 仅直链）`
- 15 行改为:`支持格式：JPG · PNG · GIF · WebP · BMP · SVG（最大 20MB）· GLB 3D 模型（最大 100MB）`
- Caddy 反代段(69 行起)内加一行说明:`> 若上传 .glb 大文件,请确认反代的请求体上限 ≥ 100MB(Caddy 默认不限制;Nginx 需 client_max_body_size 100m)。`
- 81 行「上传校验」句尾追加:`.glb 按 GLB 魔数与版本号校验。`
- API 表 105 行说明改为:`上传图片或 .glb 模型（可指定文件夹）`

- [ ] **Step 7: 验证 + Commit**

Run: `python3 -m pytest tests/ -q` → 全绿
Run: `node --check static/app.js` → 通过
交叉检查:`lightboxGlb` 在 index.html 存在;`.glb-placeholder`/`.lightbox-glb` 在 style.css 存在。

```bash
git add static/app.js static/index.html static/style.css README.md
git commit -m "feat(web): glb icon cards, lightbox placeholder, direct-link-only copy; README"
```
