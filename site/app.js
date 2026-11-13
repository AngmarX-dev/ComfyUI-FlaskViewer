let allData = [];
let viewData = [];
let filterSource = 'all';
let searchQuery = '';
let sortDir = 'desc';
let lightboxIndex = -1;
let slideshowTimer = null;
let firstLoad = true;
let activePollTimer = null;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]
  ));
}

function showToast(msg, duration) {
  duration = duration || 2800;
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('show'), duration);
}

async function fetchData() {
  try {
    const r = await fetch('/api/images', { cache: 'no-store' });
    const data = await r.json();
    const wasFirst = firstLoad;
    firstLoad = false;

    document.getElementById('live-dot').classList.remove('offline');
    document.getElementById('status').textContent = 'Live';
    document.getElementById('status').title = data.length + ' images';

    const newData = data.slice().reverse();

    if (!wasFirst && newData.length > allData.length) {
      const diff = newData.length - allData.length;
      showToast('✦ ' + diff + ' new image' + (diff > 1 ? 's' : ''));
    }

    const prevActiveFilename =
      (lightboxIndex >= 0 && viewData[lightboxIndex])
        ? viewData[lightboxIndex].filename
        : null;

    allData = newData;
    updateView();

    if (prevActiveFilename) {
      const idx = viewData.findIndex(x => x.filename === prevActiveFilename);
      if (idx >= 0) openLightbox(idx, true);
      else closeLightbox();
    }
  } catch (e) {
    document.getElementById('live-dot').classList.add('offline');
    document.getElementById('status').textContent = 'Offline';
    document.getElementById('status').title = String(e);
  }
}

function updateView() {
  let d = allData.slice();
  if (filterSource !== 'all') d = d.filter(x => x.source === filterSource);
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    d = d.filter(x => (x.prompt || '').toLowerCase().indexOf(q) >= 0);
  }
  if (sortDir === 'asc') d.reverse();
  viewData = d;
  renderGrid(); renderFilters(); updateCount();
  if (lightboxIndex >= 0) renderThumbs();
}

function renderFilters() {
  const sources = ['all'];
  allData.forEach(x => { if (sources.indexOf(x.source) < 0) sources.push(x.source); });
  const el = document.getElementById('filters');
  el.innerHTML = sources.map(s =>
    '<button class="filter' + (s === filterSource ? ' active' : '') +
    '" data-src="' + esc(s) + '">' + (s === 'all' ? 'All' : esc(s)) + '</button>'
  ).join('');
  el.querySelectorAll('.filter').forEach(b => {
    b.addEventListener('click', function() {
      filterSource = this.dataset.src; updateView();
    });
  });
}

function renderGrid() {
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty-state');
  if (!viewData.length) {
    grid.innerHTML = '';
    empty.classList.add('show');
    const filtered = allData.length && (searchQuery || filterSource !== 'all');
    document.getElementById('empty-title').textContent = filtered ? 'Nothing matches' : 'No images yet';
    document.getElementById('empty-text').innerHTML = filtered
      ? 'Try a different search or filter.'
      : 'Click <b>✦ Generate</b> or run a workflow.';
    return;
  }
  empty.classList.remove('show');
  let html = '';
  viewData.forEach((it, i) => {
    const delay = Math.min(i * 30, 400);
    const promptHtml = it.prompt ? '<div class="prompt">' + esc(it.prompt.slice(0, 200)) + '</div>' : '';
    html +=
      '<div class="card" data-i="' + i + '" style="animation-delay:' + delay + 'ms">' +
        '<div class="card-wrap">' +
          '<img src="/image/' + encodeURIComponent(it.filename) + '" loading="lazy" alt="">' +
          '<div class="card-overlay"><div class="zoom-icon">⤢</div></div>' +
        '</div>' +
        '<div class="card-meta">' +
          '<div class="t">' +
            '<span class="src-tag">' + esc(it.source) + '</span>' +
            '<span>' + esc(it.timestamp) + '</span>' +
          '</div>' + promptHtml +
        '</div>' +
      '</div>';
  });
  grid.innerHTML = html;
  grid.querySelectorAll('.card').forEach(c => {
    c.addEventListener('click', function() { openLightbox(parseInt(this.dataset.i, 10)); });
  });
}

function updateCount() {
  document.getElementById('count').textContent = viewData.length;
}

function openLightbox(i, silent) {
  if (i < 0 || i >= viewData.length) return;
  const it = viewData[i];
  lightboxIndex = i;
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lb-img');
  if (!silent) { lb.classList.remove('ready'); void img.offsetWidth; }
  img.src = '/image/' + encodeURIComponent(it.filename);
  img.alt = it.prompt || it.filename;
  document.getElementById('lb-info').innerHTML =
    '<strong>' + esc(it.source) + '</strong> · ' + esc(it.timestamp) +
    ' · #' + (it.index + 1) + '/' + it.batch +
    (it.prompt ? ' — ' + esc(it.prompt.slice(0, 200)) : '');
  const dl = document.getElementById('lb-download');
  dl.href = '/image/' + encodeURIComponent(it.filename);
  dl.download = it.filename;
  lb.classList.add('open');
  requestAnimationFrame(() => lb.classList.add('ready'));
  renderThumbs();
}

function closeLightbox() {
  const lb = document.getElementById('lightbox');
  lb.classList.remove('open', 'ready');
  stopSlideshow();
  setTimeout(() => { document.getElementById('lb-img').src = ''; }, 260);
  lightboxIndex = -1;
}

function navigate(dir) {
  if (!viewData.length || lightboxIndex < 0) return;
  const n = viewData.length;
  openLightbox(((lightboxIndex + dir) % n + n) % n, true);
}

function renderThumbs() {
  const el = document.getElementById('lb-thumbs');
  if (!viewData.length) { el.innerHTML = ''; return; }
  el.innerHTML = viewData.map((it, i) =>
    '<div class="lb-thumb' + (i === lightboxIndex ? ' active' : '') + '" data-i="' + i + '">' +
      '<img src="/image/' + encodeURIComponent(it.filename) + '" loading="lazy" alt="">' +
    '</div>'
  ).join('');
  el.querySelectorAll('.lb-thumb').forEach(t => {
    t.addEventListener('click', function() { openLightbox(parseInt(this.dataset.i, 10), true); });
  });
  const active = el.querySelector('.lb-thumb.active');
  if (active) active.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
}

function startSlideshow() {
  stopSlideshow();
  const btn = document.getElementById('lb-slideshow');
  btn.textContent = '⏸'; btn.classList.add('active');
  slideshowTimer = setInterval(() => { if (viewData.length > 1) navigate(1); }, 2200);
}
function stopSlideshow() {
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  const btn = document.getElementById('lb-slideshow');
  if (btn) { btn.textContent = '▶'; btn.classList.remove('active'); }
}
function toggleSlideshow() { slideshowTimer ? stopSlideshow() : startSlideshow(); }

function copyLink() {
  if (lightboxIndex < 0) return;
  const it = viewData[lightboxIndex];
  const url = location.origin + '/image/' + encodeURIComponent(it.filename);
  const ok = () => showToast('✓ Link copied');
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); ok(); } catch (_) { showToast('Could not copy'); }
    document.body.removeChild(ta);
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(url).then(ok, fallback);
  else fallback();
}

// ---------- Settings drawer ----------

const drawer = document.getElementById('drawer');
const setHost = document.getElementById('set-host');
const setPort = document.getElementById('set-port');
const setKeep = document.getElementById('set-keep');
const setHint = document.getElementById('set-hint');

async function openDrawer() {
  try {
    const cfg = await (await fetch('/api/config', { cache: 'no-store' })).json();
    setHost.value = cfg.host; setPort.value = cfg.port; setKeep.value = cfg.keep_history;
  } catch (e) {}
  drawer.hidden = false;
}
function closeDrawer() { drawer.hidden = true; }
function readSettings() {
  return {
    host: setHost.value.trim(),
    port: Number(setPort.value),
    keep_history: Number(setKeep.value),
  };
}
document.getElementById('settings-btn').addEventListener('click', openDrawer);
document.getElementById('set-close').addEventListener('click', closeDrawer);
drawer.addEventListener('mousedown', e => { if (e.target === drawer) closeDrawer(); });
document.getElementById('set-save').addEventListener('click', async () => {
  await fetch('/api/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(readSettings()),
  });
  setHint.textContent = 'Saved. Host/port take effect after restart.';
});
document.getElementById('set-restart').addEventListener('click', async () => {
  setHint.textContent = 'Saving…';
  await fetch('/api/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(readSettings()),
  });
  const res = await (await fetch('/api/restart', { method: 'POST' })).json();
  setHint.textContent = 'Restarting on port ' + res.port + '…';
  setTimeout(() => {
    location.href = location.protocol + '//' + location.hostname + ':' + res.port + '/';
  }, 2200);
});

// ---------- Generate drawer ----------

const genDrawer = document.getElementById('gen-drawer');
const genStatus = document.getElementById('gen-status');
const genWorkflow = document.getElementById('gen-workflow');

async function openGenDrawer() {
  try {
    const list = await (await fetch('/api/workflows', { cache: 'no-store' })).json();
    genWorkflow.innerHTML = list.map(w =>
      '<option value="' + esc(w) + '">' + esc(w.replace(/\.json$/, '')) + '</option>'
    ).join('');
    if (list.indexOf('default.json') >= 0) genWorkflow.value = 'default.json';
  } catch (e) {
    genWorkflow.innerHTML = '<option value="default.json">default</option>';
  }
  genDrawer.hidden = false;
  setTimeout(() => document.getElementById('gen-prompt').focus(), 60);
}
function closeGenDrawer() { genDrawer.hidden = true; }

document.getElementById('gen-btn').addEventListener('click', openGenDrawer);
document.getElementById('gen-close').addEventListener('click', closeGenDrawer);
genDrawer.addEventListener('mousedown', e => { if (e.target === genDrawer) closeGenDrawer(); });

document.getElementById('gen-run').addEventListener('click', async () => {
  if (activePollTimer) { clearInterval(activePollTimer); activePollTimer = null; }

  const fd = new FormData();
  fd.append('workflow', genWorkflow.value || 'default.json');
  fd.append('prompt', document.getElementById('gen-prompt').value);
  fd.append('negative_prompt', document.getElementById('gen-neg').value);
  fd.append('width', document.getElementById('gen-width').value);
  fd.append('height', document.getElementById('gen-height').value);
  fd.append('seed', document.getElementById('gen-seed').value);
  fd.append('steps', document.getElementById('gen-steps').value);
  fd.append('cfg', document.getElementById('gen-cfg').value);
  const files = document.getElementById('gen-files').files;
  for (const f of files) fd.append('images', f, f.name);

  genStatus.textContent = 'Queuing…';
  let res;
  try {
    res = await (await fetch('/api/generate', { method: 'POST', body: fd })).json();
  } catch (e) {
    genStatus.textContent = 'Error: ' + String(e);
    return;
  }
  if (res.error) { genStatus.textContent = '❌ ' + res.error; return; }

  genStatus.textContent = '⟳ Generating… (seed ' + res.seed + ')';
  const promptId = res.prompt_id;

  activePollTimer = setInterval(async () => {
    try {
      const st = await (await fetch('/api/generate/status/' + encodeURIComponent(promptId))).json();
      if (st.done) {
        clearInterval(activePollTimer); activePollTimer = null;
        if (st.error) {
          genStatus.textContent = '❌ ' + st.error;
        } else {
          const n = (st.images || []).length;
          genStatus.textContent = '✓ Done — ' + n + ' image' + (n === 1 ? '' : 's');
          fetchData();
        }
      }
    } catch (e) { /* keep polling */ }
  }, 1000);
});

// ---------- Wire-up ----------

document.getElementById('search').addEventListener('input', e => {
  searchQuery = e.target.value.trim(); updateView();
});
document.getElementById('sort-btn').addEventListener('click', e => {
  sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  e.target.textContent = sortDir === 'desc' ? '↓ Newest' : '↑ Oldest';
  updateView();
});
document.getElementById('lb-close').addEventListener('click', closeLightbox);
document.getElementById('lb-prev').addEventListener('click', () => navigate(-1));
document.getElementById('lb-next').addEventListener('click', () => navigate(1));
document.getElementById('lb-slideshow').addEventListener('click', toggleSlideshow);
document.getElementById('lb-copy').addEventListener('click', copyLink);
document.getElementById('lb-stage').addEventListener('click', e => {
  if (e.target.id === 'lb-stage') closeLightbox();
});
document.addEventListener('keydown', e => {
  if (document.getElementById('lightbox').classList.contains('open')) {
    if (e.key === 'Escape') closeLightbox();
    else if (e.key === 'ArrowLeft') navigate(-1);
    else if (e.key === 'ArrowRight') navigate(1);
    else if (e.key === ' ') { e.preventDefault(); toggleSlideshow(); }
    return;
  }
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT'
      && document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    document.getElementById('search').focus();
  }
});

fetchData();
setInterval(fetchData, 1200);