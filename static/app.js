'use strict';

/* ── Globals ──────────────────────────────────────────────────────────────── */
const uploadZone    = document.getElementById('uploadZone');
const fileInput     = document.getElementById('fileInput');
const progressWrap  = document.getElementById('progressWrap');
const progressBar   = document.getElementById('progressBar');
const progressText  = document.getElementById('progressText');
const resultsList   = document.getElementById('resultsList');
const galleryGrid   = document.getElementById('galleryGrid');
const galleryCount  = document.getElementById('galleryCount');
const galleryEmpty  = document.getElementById('galleryEmpty');
const loadMoreWrap  = document.getElementById('loadMoreWrap');
const loadMoreBtn   = document.getElementById('loadMoreBtn');
const lightbox      = document.getElementById('lightbox');
const lightboxImg   = document.getElementById('lightboxImg');
const toast         = document.getElementById('toast');
const loginOverlay  = document.getElementById('loginOverlay');
const loginForm     = document.getElementById('loginForm');
const loginPassword = document.getElementById('loginPassword');
const loginBtn      = document.getElementById('loginBtn');
const loginError    = document.getElementById('loginError');
const togglePw      = document.getElementById('togglePw');
const uploadFolder  = document.getElementById('uploadFolder');
const folderBar     = document.getElementById('folderBar');
const moveOverlay   = document.getElementById('moveOverlay');
const moveList      = document.getElementById('moveList');
const moveCancel    = document.getElementById('moveCancel');

let galleryPage = 1;
let galleryTotal = 0;
const PER_PAGE = 50;
let toastTimer = null;
let folders = [];        // [{id, name, created_at, count}]
let uncatCount = 0;
let currentFilter = 'all';  // 'all' | 'none' | <folderId>
let galleryReq = 0;

const MAX_UPLOAD_SIZE = 20 * 1024 * 1024;  // keep in sync with backend MAX_SIZE
const MAX_GLB_UPLOAD_SIZE = 100 * 1024 * 1024;  // keep in sync with backend MAX_GLB_SIZE
const ALLOWED_UPLOAD_TYPES = new Set([
  'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp', 'image/svg+xml', 'model/gltf-binary',
]);

function isGlbFile(file) { return (file.name || '').toLowerCase().endsWith('.glb'); }

// In-flight uploads keyed by a sequence id, for aggregate progress display.
const inflightUploads = new Map();
let uploadSeq = 0;

/* ── Auth ─────────────────────────────────────────────────────────────────── */
const TOKEN_KEY = 'pichost_token';

function getToken()        { return localStorage.getItem(TOKEN_KEY) || ''; }
function setToken(t)       { localStorage.setItem(TOKEN_KEY, t); }
function clearToken()      { localStorage.removeItem(TOKEN_KEY); }
function authHeader()      { return { Authorization: `Bearer ${getToken()}` }; }

function showLogin()  { loginOverlay.classList.remove('hidden'); loginPassword.focus(); }
function hideLogin()  { loginOverlay.classList.add('hidden'); }

async function handleUnauth() {
  clearToken();
  showLogin();
}

/* ── Login form ───────────────────────────────────────────────────────────── */
loginForm.addEventListener('submit', async e => {
  e.preventDefault();
  const pw = loginPassword.value.trim();
  if (!pw) return;

  loginBtn.disabled = true;
  loginBtn.textContent = '验证中…';
  loginError.textContent = '';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    if (res.ok) {
      const { token } = await res.json();
      setToken(token);
      loginPassword.value = '';
      hideLogin();
      refreshAll();
    } else {
      loginError.textContent = '密钥错误，请重试';
      loginPassword.select();
    }
  } catch {
    loginError.textContent = '网络错误，请重试';
  } finally {
    loginBtn.disabled = false;
    loginBtn.textContent = '登录';
  }
});

togglePw.addEventListener('click', () => {
  const isText = loginPassword.type === 'text';
  loginPassword.type = isText ? 'password' : 'text';
  togglePw.querySelector('svg').style.opacity = isText ? '1' : '.45';
});

/* ── Toast ────────────────────────────────────────────────────────────────── */
function showToast(msg, type = '', duration = 2200) {
  clearTimeout(toastTimer);
  toast.textContent = msg;
  toast.className = 'toast show' + (type ? ' ' + type : '');
  toastTimer = setTimeout(() => { toast.className = 'toast'; }, duration);
}

/* ── Upload zone interactions ─────────────────────────────────────────────── */
uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', e => {
  e.preventDefault();
  uploadZone.classList.add('dragging');
});
uploadZone.addEventListener('dragleave', e => {
  if (!uploadZone.contains(e.relatedTarget)) uploadZone.classList.remove('dragging');
});
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('dragging');
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/') || isGlbFile(f));
  if (files.length) uploadFiles(files);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) uploadFiles([...fileInput.files]);
  fileInput.value = '';
});

document.addEventListener('paste', e => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [...items]
    .filter(i => i.kind === 'file')
    .map(i => i.getAsFile())
    .filter(Boolean)
    .filter(f => f.type.startsWith('image/') || isGlbFile(f));
  if (files.length) {
    uploadFiles(files);
    showToast('已检测到粘贴的图片，正在上传…');
  }
});

/* ── Upload ───────────────────────────────────────────────────────────────── */
function uploadFiles(files) { files.forEach(f => uploadSingle(f)); }

// Aggregate progress across all in-flight uploads, so concurrent uploads share
// one honest bar instead of clobbering each other.
function renderUploadProgress() {
  if (inflightUploads.size === 0) {
    progressWrap.style.display = 'none';
    progressBar.style.width = '0%';
    return;
  }
  let loaded = 0, total = 0;
  for (const s of inflightUploads.values()) { loaded += s.loaded; total += s.total; }
  const pct = total ? Math.round((loaded / total) * 100) : 0;
  progressWrap.style.display = 'block';
  progressBar.style.width = pct + '%';
  progressText.textContent = inflightUploads.size > 1
    ? `${pct}% · ${inflightUploads.size} 个文件`
    : `${pct}%`;
}

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

  const formData = new FormData();
  formData.append('file', file);

  const id = ++uploadSeq;
  inflightUploads.set(id, { loaded: 0, total: file.size || 0 });
  renderUploadProgress();
  const finish = () => { inflightUploads.delete(id); renderUploadProgress(); };

  const xhr = new XMLHttpRequest();
  const targetFolder = uploadFolder.value;
  xhr.open('POST', '/api/upload' + (targetFolder ? `?folder_id=${encodeURIComponent(targetFolder)}` : ''));
  xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`);

  xhr.upload.addEventListener('progress', e => {
    if (e.lengthComputable) {
      inflightUploads.set(id, { loaded: e.loaded, total: e.total });
      renderUploadProgress();
    }
  });

  xhr.addEventListener('load', () => {
    finish();
    if (xhr.status === 200) {
      const data = JSON.parse(xhr.responseText);
      prependResult(data);
      const matches = currentFilter === 'all'
        || (currentFilter === 'none' && !data.folder_id)
        || currentFilter === data.folder_id;
      if (matches) {
        prependGalleryItem(data);
        galleryTotal++;
        updateGalleryCount();
        galleryEmpty.style.display = 'none';
      }
      loadFolders();
      showToast('上传成功！', 'success');
    } else if (xhr.status === 401) {
      handleUnauth();
    } else {
      let msg = '上传失败';
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch(_) {}
      showToast(msg, 'error');
    }
  });

  xhr.addEventListener('error', () => {
    finish();
    showToast('网络错误，上传失败', 'error');
  });

  xhr.send(formData);
}

/* ── Authenticated fetch wrapper ──────────────────────────────────────────── */
async function authFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeader(), ...(options.headers || {}) },
  });
  if (res.status === 401) { handleUnauth(); return null; }
  return res;
}

/* ── Result panel ─────────────────────────────────────────────────────────── */
// Build the copyable link snippets. The name is attacker-controlled (it is the
// original upload filename), so escape it for the context it lands in.
function buildLinks(url, name) {
  const n = name || '';
  return [
    { label: '直链',      value: url },
    { label: 'BBCode',   value: `[img]${url}[/img]` },
    { label: 'Markdown', value: `![${escMarkdown(n)}](${url})` },
    { label: 'HTML',     value: `<img src="${url}" alt="${escHtml(n)}">` },
  ];
}

function prependResult(data) {
  const origin = window.location.origin;
  const url    = `${origin}/uploads/${data.filename}`;

  const links = buildLinks(url, data.orig_name || data.filename);

  const item = document.createElement('div');
  item.className = 'result-item';
  item.innerHTML = `
    <div class="result-inner">
      <div class="result-preview">
        <img src="/uploads/thumbs/${data.filename}"
             onerror="this.src='/uploads/${data.filename}'"
             alt="${escHtml(data.orig_name || data.filename)}" />
      </div>
      <div class="result-links">
        <div class="result-meta">
          <span>${escHtml(data.orig_name || data.filename)}</span>
          <span>${formatSize(data.size)}</span>
          ${data.width ? `<span>${data.width}×${data.height}</span>` : ''}
        </div>
        ${links.map(l => linkRow(l.label, l.value)).join('')}
      </div>
    </div>`;

  resultsList.prepend(item);

  item.querySelectorAll('.btn-copy').forEach(btn => {
    btn.addEventListener('click', () => copyText(btn.dataset.value, btn));
  });
}

function linkRow(label, value) {
  return `
    <div class="link-row">
      <span class="link-label">${escHtml(label)}</span>
      <span class="link-input" title="${escAttr(value)}">${escHtml(value)}</span>
      <button class="btn-copy" data-value="${escAttr(value)}">复制</button>
    </div>`;
}

/* ── Folders ──────────────────────────────────────────────────────────────── */
async function loadFolders() {
  const res = await authFetch('/api/folders');
  if (!res || !res.ok) return;
  const data = await res.json();
  folders = data.folders;
  uncatCount = data.uncategorized;
  renderFolderBar();
  renderUploadSelect();
}

function folderChip(filter, label, count) {
  const active = currentFilter === filter ? ' active' : '';
  const badge = count == null ? '' : `<span class="folder-chip-count">${count}</span>`;
  return `<button class="folder-chip${active}"
                  data-filter="${escAttr(filter)}">${escHtml(label)}${badge}</button>`;
}

function renderFolderBar() {
  const total = folders.reduce((n, f) => n + f.count, 0) + uncatCount;
  let html = folderChip('all', '全部', total) + folderChip('none', '未分类', uncatCount);
  html += folders.map(f => folderChip(f.id, f.name, f.count)).join('');
  html += `<button class="folder-chip folder-chip-new" data-action="new">＋ 新建文件夹</button>`;

  const selected = folders.find(f => f.id === currentFilter);
  if (selected) {
    html += `
      <span class="chip-actions">
        <button class="chip-btn" data-action="rename">重命名</button>
        <button class="chip-btn chip-btn-danger" data-action="remove">删除</button>
      </span>`;
  }
  folderBar.innerHTML = html;
}

function renderUploadSelect() {
  const prev = uploadFolder.value;
  uploadFolder.innerHTML = '<option value="">未分类</option>' +
    folders.map(f => `<option value="${escAttr(f.id)}">${escHtml(f.name)}</option>`).join('');
  if ([...uploadFolder.options].some(o => o.value === prev)) uploadFolder.value = prev;
}

async function refreshAll() {
  await Promise.all([loadFolders(), loadGallery(1)]);
}

folderBar.addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'new')    { createFolder(); return; }
  if (action === 'rename') { renameFolder(currentFilter); return; }
  if (action === 'remove') { removeFolder(currentFilter); return; }
  if (btn.dataset.filter && btn.dataset.filter !== currentFilter) {
    currentFilter = btn.dataset.filter;
    renderFolderBar();
    loadGallery(1);
  }
});

async function createFolder() {
  const name = (prompt('新文件夹名称:') || '').trim();
  if (!name) return;
  const res = await authFetch('/api/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res) return;
  if (res.ok || res.status === 201) {
    showToast('文件夹已创建', 'success');
    loadFolders();
  } else {
    const err = await res.json().catch(() => ({}));
    showToast(typeof err.detail === 'string' ? err.detail : '文件夹名称需为 1-50 个字符', 'error');
  }
}

async function renameFolder(id) {
  const cur = folders.find(f => f.id === id);
  if (!cur) return;
  const name = (prompt('重命名文件夹:', cur.name) || '').trim();
  if (!name || name === cur.name) return;
  const res = await authFetch(`/api/folders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res) return;
  if (res.ok) {
    showToast('已重命名', 'success');
    loadFolders();
  } else {
    const err = await res.json().catch(() => ({}));
    showToast(typeof err.detail === 'string' ? err.detail : '文件夹名称需为 1-50 个字符', 'error');
  }
}

async function removeFolder(id) {
  const cur = folders.find(f => f.id === id);
  if (!cur) return;
  if (!confirm(`删除文件夹「${cur.name}」？其中的图片将回到「未分类」。`)) return;
  const res = await authFetch(`/api/folders/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res) return;
  if (res.ok) {
    showToast('文件夹已删除', 'success');
    if (currentFilter === id) currentFilter = 'all';
    refreshAll();
  } else {
    const err = await res.json().catch(() => ({}));
    showToast(typeof err.detail === 'string' ? err.detail : '删除失败', 'error');
  }
}

/* ── Gallery ──────────────────────────────────────────────────────────────── */
async function loadGallery(page = 1, append = false) {
  const seq = ++galleryReq;
  const folderQ = currentFilter === 'all' ? '' : `&folder=${encodeURIComponent(currentFilter)}`;
  const res = await authFetch(`/api/images?page=${page}&per_page=${PER_PAGE}${folderQ}`);
  if (!res || seq !== galleryReq) return;

  try {
    const data = await res.json();
    if (seq !== galleryReq) return;
    galleryTotal = data.total;
    updateGalleryCount();

    // Keep the paging cursor in sync with a full (re)load, otherwise a later
    // "load more" resumes from a stale page and skips rows.
    if (!append) { galleryPage = page; galleryGrid.innerHTML = ''; }

    if (data.images.length === 0 && page === 1) {
      galleryGrid.appendChild(galleryEmpty);
      galleryEmpty.style.display = 'flex';
    } else {
      galleryEmpty.style.display = 'none';
      data.images.forEach(img => appendGalleryItem(img));
    }

    const shown = (page - 1) * PER_PAGE + data.images.length;
    loadMoreWrap.style.display = shown < data.total ? 'block' : 'none';
  } catch {
    showToast('加载画廊失败', 'error');
  }
}

function galleryHas(id) {
  return !!galleryGrid.querySelector(`.gallery-item[data-id="${CSS.escape(String(id))}"]`);
}
function appendGalleryItem(data)  { if (!galleryHas(data.id)) galleryGrid.appendChild(buildGalleryItem(data)); }
function prependGalleryItem(data) { if (!galleryHas(data.id)) galleryGrid.prepend(buildGalleryItem(data)); }

function buildGalleryItem(data) {
  const url  = `${window.location.origin}/uploads/${data.filename}`;
  const item = document.createElement('div');
  item.className = 'gallery-item';
  item.dataset.id = data.id;
  item.innerHTML = `
    <img src="/uploads/thumbs/${data.filename}"
         onerror="this.src='/uploads/${data.filename}'"
         alt="${escHtml(data.orig_name || data.filename)}" loading="lazy" />
    <div class="gallery-overlay">
      <span class="gallery-item-name">${escHtml(data.orig_name || data.filename)}</span>
      <button class="btn-move" data-id="${escAttr(data.id)}">移动</button>
      <button class="btn-delete" data-id="${escAttr(data.id)}">删除</button>
    </div>`;

  item.addEventListener('click', () => openLightbox(url, data.orig_name || data.filename));
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

function updateGalleryCount() {
  galleryCount.textContent = galleryTotal > 0 ? `共 ${galleryTotal} 张` : '';
}

loadMoreBtn.addEventListener('click', () => {
  galleryPage++;
  loadGallery(galleryPage, true);
});

/* ── Move image ───────────────────────────────────────────────────────────── */
let movingImage = null;  // {id, folderId, itemEl, data}

function openMoveModal(data, itemEl) {
  movingImage = { id: data.id, folderId: data.folder_id || '', itemEl, data };
  const options = [{ id: '', name: '未分类' }, ...folders];
  moveList.innerHTML = options.map(f => `
    <button data-folder="${escAttr(f.id)}"
            ${f.id === movingImage.folderId ? 'disabled' : ''}>
      <span>${escHtml(f.name)}</span>${f.id === movingImage.folderId ? '<span>当前</span>' : ''}
    </button>`).join('');
  moveOverlay.classList.remove('hidden');
}

function closeMoveModal() {
  moveOverlay.classList.add('hidden');
  movingImage = null;
}

moveList.addEventListener('click', async e => {
  const btn = e.target.closest('button[data-folder]');
  if (!btn || !movingImage) return;
  const target = btn.dataset.folder || null;
  const { id, itemEl, data } = movingImage;
  closeMoveModal();  // synchronously null movingImage: re-entrant clicks bail on the null check above
  const res = await authFetch(`/api/images/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder_id: target }),
  });
  if (!res) return;
  if (res.ok) {
    data.folder_id = target;  // keep the cached item in sync for the next move
    const stillVisible = currentFilter === 'all'
      || (currentFilter === 'none' && !target)
      || currentFilter === target;
    if (!stillVisible) {
      itemEl.remove();
      galleryTotal = Math.max(0, galleryTotal - 1);
      updateGalleryCount();
      if (galleryGrid.querySelectorAll('.gallery-item').length === 0) {
        galleryGrid.appendChild(galleryEmpty);
        galleryEmpty.style.display = 'flex';
      }
    }
    loadFolders();
    showToast('已移动', 'success');
  } else {
    const err = await res.json().catch(() => ({}));
    showToast(typeof err.detail === 'string' ? err.detail : '移动失败', 'error');
  }
});

moveCancel.addEventListener('click', closeMoveModal);
moveOverlay.addEventListener('click', e => { if (e.target === moveOverlay) closeMoveModal(); });

/* ── Delete ───────────────────────────────────────────────────────────────── */
async function confirmDelete(id, itemEl) {
  if (!confirm('确定要删除这张图片吗？此操作不可撤销。')) return;
  const res = await authFetch(`/api/images/${id}`, { method: 'DELETE' });
  if (!res) return;
  if (res.ok) {
    itemEl.style.transition = 'opacity .3s, transform .3s';
    itemEl.style.opacity = '0';
    itemEl.style.transform = 'scale(.85)';
    setTimeout(() => itemEl.remove(), 300);
    galleryTotal = Math.max(0, galleryTotal - 1);
    updateGalleryCount();
    if (galleryGrid.querySelectorAll('.gallery-item').length === 0) {
      galleryGrid.appendChild(galleryEmpty);
      galleryEmpty.style.display = 'flex';
    }
    showToast('图片已删除', 'success');
  } else {
    showToast('删除失败', 'error');
  }
}

/* ── Lightbox ─────────────────────────────────────────────────────────────── */
function openLightbox(url, name) {
  lightboxImg.src = url;
  lightbox.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  const links = buildLinks(url, name);

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
  document.body.style.overflow = '';
}
document.getElementById('lightboxClose').addEventListener('click', closeLightbox);
document.getElementById('lightboxBackdrop').addEventListener('click', closeLightbox);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeLightbox(); closeMoveModal(); }
});

/* ── Copy helper ──────────────────────────────────────────────────────────── */
function copyText(text, btn) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => flashCopied(btn));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    flashCopied(btn);
  }
}
function flashCopied(btn) {
  const orig = btn.textContent;
  btn.textContent = '已复制 ✓';
  btn.classList.add('copied');
  setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1800);
}

/* ── Utils ────────────────────────────────────────────────────────────────── */
function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escAttr(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escMarkdown(str) {
  return String(str).replace(/[\[\]\\]/g, '\\$&').replace(/[\r\n]+/g, ' ');
}

/* ── Nav active state ─────────────────────────────────────────────────────── */
const navLinks = document.querySelectorAll('.nav-link');
const sections = document.querySelectorAll('#upload-section, #gallery-section');

function setActiveNav(id) {
  navLinks.forEach(l => l.classList.remove('active'));
  const active = document.querySelector(`.nav-link[href="#${id}"]`);
  if (active) active.classList.add('active');
}

// Update on click immediately
navLinks.forEach(link => {
  link.addEventListener('click', () => {
    const id = link.getAttribute('href').slice(1);
    setActiveNav(id);
  });
});

// Also update on scroll
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) setActiveNav(entry.target.id);
  });
}, { rootMargin: '-60px 0px -60% 0px', threshold: 0 });
sections.forEach(s => observer.observe(s));

/* ── Init ─────────────────────────────────────────────────────────────────── */
if (getToken()) {
  hideLogin();
  refreshAll();
} else {
  showLogin();
}
