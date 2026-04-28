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

let galleryPage = 1;
let galleryTotal = 0;
const PER_PAGE = 50;
let toastTimer = null;

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
      loadGallery(1);
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
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/'));
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
    .filter(i => i.kind === 'file' && i.type.startsWith('image/'))
    .map(i => i.getAsFile())
    .filter(Boolean);
  if (files.length) {
    uploadFiles(files);
    showToast('已检测到粘贴的图片，正在上传…');
  }
});

/* ── Upload ───────────────────────────────────────────────────────────────── */
function uploadFiles(files) { files.forEach(f => uploadSingle(f)); }

function uploadSingle(file) {
  const formData = new FormData();
  formData.append('file', file);

  progressWrap.style.display = 'block';
  progressBar.style.width = '0%';
  progressText.textContent = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`);

  xhr.upload.addEventListener('progress', e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      progressBar.style.width = pct + '%';
      progressText.textContent = pct + '%';
    }
  });

  xhr.addEventListener('load', () => {
    progressWrap.style.display = 'none';
    if (xhr.status === 200) {
      const data = JSON.parse(xhr.responseText);
      prependResult(data);
      prependGalleryItem(data);
      galleryTotal++;
      updateGalleryCount();
      galleryEmpty.style.display = 'none';
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
    progressWrap.style.display = 'none';
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
function prependResult(data) {
  const origin = window.location.origin;
  const url    = `${origin}/uploads/${data.filename}`;

  const links = [
    { label: '直链',      value: url },
    { label: 'BBCode',   value: `[img]${url}[/img]` },
    { label: 'Markdown', value: `![${data.orig_name || data.filename}](${url})` },
    { label: 'HTML',     value: `<img src="${url}" alt="${data.orig_name || data.filename}">` },
  ];

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

/* ── Gallery ──────────────────────────────────────────────────────────────── */
async function loadGallery(page = 1, append = false) {
  const res = await authFetch(`/api/images?page=${page}&per_page=${PER_PAGE}`);
  if (!res) return;

  try {
    const data = await res.json();
    galleryTotal = data.total;
    updateGalleryCount();

    if (!append) galleryGrid.innerHTML = '';

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

function appendGalleryItem(data)  { galleryGrid.appendChild(buildGalleryItem(data)); }
function prependGalleryItem(data) { galleryGrid.prepend(buildGalleryItem(data)); }

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
      <button class="btn-delete" data-id="${escAttr(data.id)}">删除</button>
    </div>`;

  item.addEventListener('click', () => openLightbox(url, data.orig_name || data.filename));
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

  const links = [
    { label: '直链',      value: url },
    { label: 'BBCode',   value: `[img]${url}[/img]` },
    { label: 'Markdown', value: `![${name}](${url})` },
    { label: 'HTML',     value: `<img src="${url}" alt="${name}">` },
  ];

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
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

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
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
  loadGallery(1);
} else {
  showLogin();
}
