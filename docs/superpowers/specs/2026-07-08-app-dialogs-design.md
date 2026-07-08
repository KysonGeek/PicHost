# PicHost 应用内统一对话框设计

日期:2026-07-08
状态:已确认

## 目标

淘汰浏览器原生 `prompt()`/`confirm()`,用与整体设计语言一致的应用内弹窗承载四个交互:新建文件夹、重命名文件夹、删除文件夹、删除图片。

## 背景

文件夹管理当前用原生 `prompt`/`confirm`,画廊删除图片用原生 `confirm`;它们与应用的登录卡片、「移动到文件夹」弹窗的视觉语言完全脱节(用户反馈「与整体风格不符」)。

## 组件

一个通用对话框,复用 move-overlay/move-card 的视觉规格(遮罩色、卡片圆角 `--radius`、阴影 `--shadow-lg`、按钮风格)。

### HTML(index.html,`<!-- Toast -->` 前)

```html
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

### JS API(app.js,Promise 风格)

- `showInputDialog({title, value = '', confirmText = '确定'}) → Promise<string|null>`
  - 确定:返回去空白后的字符串;取消/Esc/点遮罩:返回 null。
  - 内置校验:去空白后非空且 ≤50 字符(与后端 FolderName 规则一致);不合法在 `#dialogError` 行内红字提示,不关窗。
  - 打开即聚焦输入框并全选;Enter 提交。
- `showConfirmDialog({title, message, confirmText = '确定', danger = false}) → Promise<boolean>`
  - danger 时主按钮红色(`--danger`)。
- 单例:同一时间只开一个;打开时暂不与 lightbox/move modal 叠加(调用点天然互斥)。
- Esc 关闭:并入现有全局 keydown(与 closeLightbox/closeMoveModal 并列 closeDialog(cancel 语义))。

## 四个调用点的替换(逻辑不变,仅换交互层)

| 调用点 | 现状 | 替换为 |
|---|---|---|
| createFolder | `prompt('新文件夹名称:')` | `showInputDialog({title: '新建文件夹'})` |
| renameFolder | `prompt('重命名文件夹:', cur.name)` | `showInputDialog({title: '重命名文件夹', value: cur.name})` |
| removeFolder | `confirm('删除文件夹「X」?…')` | `showConfirmDialog({title: '删除文件夹', message: '删除「X」?其中的图片将回到「未分类」。', confirmText: '删除', danger: true})` |
| confirmDelete(图片) | `confirm('确定要删除这张图片吗?…')` | `showConfirmDialog({title: '删除图片', message: '确定要删除这张图片吗?此操作不可撤销。', confirmText: '删除', danger: true})` |

- renameFolder 保留「新名与旧名相同则不请求」的现有短路。
- 后端错误(409 重名、404 等)仍走现有 toast 路径,弹窗在用户确认后即关闭。
- 文件夹名等用户可控内容进入 message 时用 textContent 赋值(不走 innerHTML),无转义负担。

## CSS

新增 `.dialog-overlay/.dialog-card/.dialog-title/.dialog-message/.dialog-input/.dialog-error/.dialog-actions/.dialog-btn(.dialog-cancel/.dialog-confirm/.dialog-danger)`,规格对齐 move-card 与登录输入框(`.login-input` 的 focus 描边风格)。

## 测试与验证

- 无 JS 测试基建:`node --check static/app.js`;后端套件回归(54 个,应零影响)。
- Playwright 截图脚本:登录后依次触发 新建/重命名/删除文件夹/删除图片 四个弹窗各截图,人工核对风格一致;并验证 Enter 提交、Esc 取消、空名/超长名的行内报错。
