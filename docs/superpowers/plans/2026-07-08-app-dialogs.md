# 应用内统一对话框 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用与整体风格一致的应用内弹窗替换四处浏览器原生 prompt/confirm(新建/重命名/删除文件夹、删除图片)。

**Architecture:** 一个通用对话框组件(遮罩 + 卡片,复用 move-modal 的视觉规格),Promise 风格 API:`showInputDialog` / `showConfirmDialog`。四个调用点仅替换交互层,API 调用、toast、刷新逻辑不动。

**Tech Stack:** 原生 JS/CSS(无框架);验证用 node --check + Playwright 截图(scratchpad 已装好 playwright)。

**Spec:** `docs/superpowers/specs/2026-07-08-app-dialogs-design.md`

## Global Constraints

- 输入校验与后端 FolderName 一致:去空白后非空且 ≤50 字符;不合法在弹窗内行内红字提示「名称需为 1-50 个字符」,不关窗。
- 取消语义:输入弹窗返回 `null`,确认弹窗返回 `false`;Esc / 点遮罩 = 取消。
- 用户可控文本(文件夹名)进入弹窗一律 `textContent` 赋值,不走 innerHTML。
- 危险操作(两个删除)主按钮红色(`--danger`),confirmText「删除」。
- 后端错误(409/404)仍走现有 toast 路径;弹窗在用户确认后即关闭。
- 视觉规格对齐 move-card:遮罩 `rgba(15,23,42,.55)`、卡片 `var(--radius)` 圆角、`var(--shadow-lg)` 阴影、宽 `min(92vw, 360px)`。
- 后端文件不得改动;`python3 -m pytest tests/ -q` 保持 54 通过。
- Playwright 环境:`cd /tmp/claude-0/-opt-app-img/6a881bff-f4f3-4b03-93d2-505e6bd69f66/scratchpad` 下运行 node 脚本(playwright 已安装);隔离起服:`PICHOST_PASSWORD=shot-pw PICHOST_DB_PATH=$PWD/pichost-run/img.db PICHOST_UPLOAD_DIR=$PWD/pichost-run/uploads uvicorn main:app --app-dir /opt/app/img --port 8124`,用完 `pkill -f "port 8124"`。**绝不能碰 8000 端口上用户自己的服务。**

---

### Task 1: 对话框组件(HTML + CSS + JS)

**Files:**
- Modify: `static/index.html`(`<!-- Toast -->` 之前加弹窗骨架)
- Modify: `static/app.js`(新增 App dialog 段;扩展全局 Escape 处理,当前在 623-625 行)
- Modify: `static/style.css`(文件末尾加样式)

**Interfaces:**
- Produces(Task 2 依赖):
  - `showInputDialog({title, value = '', confirmText = '确定'}) → Promise<string|null>`(返回去空白后的合法名称,取消为 null)
  - `showConfirmDialog({title, message, confirmText = '确定', danger = false}) → Promise<boolean>`
  - DOM id:`dialogOverlay/dialogTitle/dialogMessage/dialogInput/dialogError/dialogCancel/dialogConfirm`

- [ ] **Step 1: index.html 骨架**

`<!-- Toast -->` 之前加:

```html
  <!-- App dialog (input / confirm) -->
  <div class="dialog-overlay hidden" id="dialogOverlay">
    <div class="dialog-card">
      <h3 class="dialog-title" id="dialogTitle"></h3>
      <p class="dialog-message" id="dialogMessage" style="display:none"></p>
      <input class="dialog-input" id="dialogInput" style="display:none" maxlength="50" />
      <p class="dialog-error" id="dialogError"></p>
      <div class="dialog-actions">
        <button class="dialog-btn dialog-cancel" id="dialogCancel">取消</button>
        <button class="dialog-btn dialog-confirm" id="dialogConfirm">确定</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: app.js 组件段**

在 `/* ── Lightbox ── */` 段之前新增一段:

```js
/* ── App dialog (input / confirm) ─────────────────────────────────────────── */
const dialogOverlay = document.getElementById('dialogOverlay');
const dialogTitle   = document.getElementById('dialogTitle');
const dialogMessage = document.getElementById('dialogMessage');
const dialogInput   = document.getElementById('dialogInput');
const dialogError   = document.getElementById('dialogError');
const dialogCancel  = document.getElementById('dialogCancel');
const dialogConfirm = document.getElementById('dialogConfirm');

let dialogResolve = null;   // pending promise resolver; null = no dialog open
let dialogMode = null;      // 'input' | 'confirm'

function openDialog({ title, message = '', input = false, value = '', confirmText = '确定', danger = false }) {
  dialogTitle.textContent = title;
  dialogMessage.textContent = message;   // user-controlled text stays textContent
  dialogMessage.style.display = message ? '' : 'none';
  dialogInput.style.display = input ? '' : 'none';
  dialogInput.value = value;
  dialogError.textContent = '';
  dialogConfirm.textContent = confirmText;
  dialogConfirm.classList.toggle('dialog-danger', danger);
  dialogMode = input ? 'input' : 'confirm';
  dialogOverlay.classList.remove('hidden');
  if (input) { dialogInput.focus(); dialogInput.select(); }
  else dialogConfirm.focus();
  return new Promise(resolve => { dialogResolve = resolve; });
}

function settleDialog(result) {
  if (!dialogResolve) return;  // no dialog open — safe to call blindly (Esc handler)
  const resolve = dialogResolve;
  dialogResolve = null;
  dialogOverlay.classList.add('hidden');
  resolve(result);
}

function showInputDialog({ title, value = '', confirmText = '确定' }) {
  return openDialog({ title, input: true, value, confirmText });
}

function showConfirmDialog({ title, message, confirmText = '确定', danger = false }) {
  return openDialog({ title, message, confirmText, danger });
}

function submitDialog() {
  if (dialogMode === 'input') {
    const name = dialogInput.value.trim();
    if (!name || name.length > 50) {
      dialogError.textContent = '名称需为 1-50 个字符';
      dialogInput.focus();
      return;  // keep the dialog open
    }
    settleDialog(name);
  } else {
    settleDialog(true);
  }
}

function cancelDialog() { settleDialog(dialogMode === 'input' ? null : false); }

dialogConfirm.addEventListener('click', submitDialog);
dialogCancel.addEventListener('click', cancelDialog);
dialogOverlay.addEventListener('click', e => { if (e.target === dialogOverlay) cancelDialog(); });
dialogInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); submitDialog(); }
});
```

全局 Escape 处理(约 623-625 行)改为三连:

```js
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeLightbox(); closeMoveModal(); cancelDialog(); }
});
```

- [ ] **Step 3: style.css**

文件末尾追加:

```css
/* ── App dialog ──────────────────────────────────────────────────────────── */
.dialog-overlay {
  position: fixed; inset: 0; z-index: 1300;
  background: rgba(15,23,42,.55);
  display: flex; align-items: center; justify-content: center;
}
.dialog-overlay.hidden { display: none; }
.dialog-card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow-lg); padding: 24px; width: min(92vw, 360px);
}
.dialog-title { font-size: 1.05rem; margin-bottom: 14px; }
.dialog-message { color: var(--text-muted); font-size: .92rem; line-height: 1.6; margin-bottom: 4px; }
.dialog-input {
  width: 100%; padding: 10px 14px; border: 1px solid var(--border);
  border-radius: 10px; font-size: .95rem; background: var(--surface2);
  color: var(--text); outline: none;
}
.dialog-input:focus { border-color: var(--primary); }
.dialog-error { color: var(--danger); font-size: .8rem; min-height: 1.1em; margin-top: 6px; }
.dialog-actions { display: flex; gap: 10px; margin-top: 16px; }
.dialog-btn {
  flex: 1; padding: 9px; border: none; border-radius: 10px;
  font-size: .9rem; cursor: pointer; transition: all .15s;
}
.dialog-cancel { background: var(--surface2); color: var(--text-muted); }
.dialog-cancel:hover { color: var(--text); }
.dialog-confirm { background: var(--primary); color: #fff; }
.dialog-confirm:hover { background: var(--primary-dark); }
.dialog-confirm.dialog-danger { background: var(--danger); }
.dialog-confirm.dialog-danger:hover { background: #dc2626; }
```

- [ ] **Step 4: 组件级视觉验证**

`node --check static/app.js` 通过后,在 scratchpad 启动隔离服务(见 Global Constraints)并运行以下脚本(存为 scratchpad/dialog-smoke.js):

```js
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

  await page.goto('http://localhost:8124/');
  await page.fill('#loginPassword', 'shot-pw');
  await page.click('#loginBtn');
  await page.waitForSelector('.folder-chip');

  // input dialog
  await page.evaluate("void showInputDialog({ title: '新建文件夹', confirmText: '创建' })");
  await page.waitForSelector('.dialog-card:visible');
  await page.locator('.dialog-card').screenshot({ path: 'dialog-input.png' });
  // empty submit keeps it open with inline error
  await page.click('#dialogConfirm');
  const err = await page.textContent('#dialogError');
  console.log('inline error shown:', err);
  await page.locator('.dialog-card').screenshot({ path: 'dialog-input-error.png' });
  await page.keyboard.press('Escape');

  // danger confirm dialog
  await page.evaluate("void showConfirmDialog({ title: '删除文件夹', message: '删除「旅行」？其中的图片将回到「未分类」。', confirmText: '删除', danger: true })");
  await page.waitForSelector('.dialog-card:visible');
  await page.locator('.dialog-card').screenshot({ path: 'dialog-confirm-danger.png' });
  await page.keyboard.press('Escape');

  console.log('console errors:', errors.length ? errors : 'none');
  await browser.close();
})();
```

Expected:`inline error shown: 名称需为 1-50 个字符`;`console errors: none`;三张截图与登录卡/移动弹窗风格一致(Read 截图核对)。跑完 `pkill -f "port 8124"`,并确认 `pgrep -f "uvicorn main:app --app-dir"` 无残留(8000 端口进程不得出现在结果中)。

- [ ] **Step 5: 全量回归 + Commit**

Run: `python3 -m pytest tests/ -q` → 54 passed

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat(web): in-app dialog component (input/confirm, danger variant)"
```

---

### Task 2: 替换四个原生弹窗调用点

**Files:**
- Modify: `static/app.js:376-428`(createFolder / renameFolder / removeFolder)
- Modify: `static/app.js:565-566`(confirmDelete 的 confirm 行)

**Interfaces:**
- Consumes: Task 1 的 `showInputDialog` / `showConfirmDialog`。

- [ ] **Step 1: createFolder / renameFolder**

`createFolder` 的第一二行:

```js
  const name = await showInputDialog({ title: '新建文件夹', confirmText: '创建' });
  if (!name) return;
```

`renameFolder` 对应两行:

```js
  const name = await showInputDialog({ title: '重命名文件夹', value: cur.name, confirmText: '保存' });
  if (!name || name === cur.name) return;
```

(其余 fetch/toast 逻辑逐字保留。)

- [ ] **Step 2: removeFolder / confirmDelete**

`removeFolder` 的 confirm 行替换为:

```js
  const ok = await showConfirmDialog({
    title: '删除文件夹',
    message: `删除「${cur.name}」？其中的图片将回到「未分类」。`,
    confirmText: '删除', danger: true,
  });
  if (!ok) return;
```

`confirmDelete` 的 confirm 行替换为:

```js
  const ok = await showConfirmDialog({
    title: '删除图片',
    message: '确定要删除这张图片吗？此操作不可撤销。',
    confirmText: '删除', danger: true,
  });
  if (!ok) return;
```

(message 值经模板字符串进入 `textContent`,无需转义。)

- [ ] **Step 3: 端到端视觉验证**

隔离起服(同 Task 1),运行以下脚本(存为 scratchpad/dialog-e2e.js):

```js
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('dialog', d => { errors.push('NATIVE DIALOG APPEARED: ' + d.message); d.dismiss(); });

  await page.goto('http://localhost:8124/');
  await page.fill('#loginPassword', 'shot-pw');
  await page.click('#loginBtn');
  await page.waitForSelector('.folder-chip');

  // create via ＋新建文件夹 → styled dialog → Enter submits
  await page.click('.folder-chip-new');
  await page.waitForSelector('.dialog-card:visible');
  await page.screenshot({ path: 'e2e-create.png' });
  await page.fill('#dialogInput', '测试夹');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.folder-chip:has-text("测试夹")');

  // select it → rename dialog prefilled → Esc cancels
  await page.click('.folder-chip:has-text("测试夹")');
  await page.click('.chip-btn[data-action="rename"]');
  await page.waitForSelector('.dialog-card:visible');
  const prefill = await page.inputValue('#dialogInput');
  console.log('rename prefill:', prefill);
  await page.screenshot({ path: 'e2e-rename.png' });
  await page.keyboard.press('Escape');

  // delete folder → danger confirm → confirm removes chip
  await page.click('.chip-btn[data-action="remove"]');
  await page.waitForSelector('.dialog-card:visible');
  await page.screenshot({ path: 'e2e-remove-folder.png' });
  await page.click('#dialogConfirm');
  await page.waitForSelector('.folder-chip:has-text("测试夹")', { state: 'detached' });

  console.log('console errors:', errors.length ? errors : 'none');
  await browser.close();
})();
```

Expected:`rename prefill: 测试夹`;`console errors: none`(**尤其不得出现 NATIVE DIALOG APPEARED**);截图风格统一(Read 核对)。跑完 `pkill -f "port 8124"` 清理。

- [ ] **Step 4: 全量回归 + Commit**

Run: `python3 -m pytest tests/ -q` → 54 passed
Run: `node --check static/app.js` → 通过
Run: `grep -n "prompt(\|confirm(" static/app.js` → 无原生调用残留(只允许出现在注释/不出现)

```bash
git add static/app.js
git commit -m "feat(web): replace native prompt/confirm with in-app dialogs"
```
