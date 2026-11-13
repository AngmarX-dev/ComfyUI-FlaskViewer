import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STYLE_ID = "fv-panel-styles";

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement("style");
  s.id = STYLE_ID;
  s.textContent = `
    .fv-wrap { display:flex; flex-direction:column; height:100%; min-height:0; }
    .fv-bar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px;
              border-bottom:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.02); }
    .fv-dot { width:8px; height:8px; border-radius:50%; background:#ef4444;
              box-shadow:0 0 6px #ef4444; flex:0 0 auto; }
    .fv-dot.on { background:#10b981; box-shadow:0 0 6px #10b981; }
    .fv-status { font-size:11px; color:#aaa; }
    .fv-url { font-size:11px; color:#6cf; overflow:hidden; text-overflow:ellipsis;
              white-space:nowrap; max-width:100%; }
    .fv-spacer { flex:1 1 auto; }
    .fv-btn { font-size:11px; padding:4px 9px; border-radius:7px;
              border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.04);
              color:#eee; cursor:pointer; transition:background .15s; }
    .fv-btn:hover { background:rgba(255,255,255,.1); }
    .fv-btn.primary { background:linear-gradient(135deg,#8b5cf6,#22d3ee);
                      border-color:transparent; color:#fff; }
    .fv-settings { display:flex; flex-wrap:wrap; gap:8px; padding:8px;
                   border-bottom:1px solid rgba(255,255,255,.08);
                   background:rgba(255,255,255,.02); }
    .fv-settings label { display:flex; flex-direction:column; font-size:10px;
                         color:#aaa; gap:2px; }
    .fv-settings input { font-size:11px; padding:4px 6px; border-radius:6px;
                         border:1px solid rgba(255,255,255,.12);
                         background:rgba(0,0,0,.3); color:#eee; width:90px; }
    .fv-body { flex:1 1 auto; min-height:0; position:relative; background:#0a0a0f; }
    .fv-frame { position:absolute; inset:0; width:100%; height:100%; border:0;
                background:#0a0a0f; }
    .fv-empty { position:absolute; inset:0; display:flex; flex-direction:column;
                align-items:center; justify-content:center; gap:10px; color:#888;
                font-size:12px; text-align:center; padding:20px; }
    .fv-overlay { position:fixed; inset:0; z-index:99999;
                  background:rgba(0,0,0,.85); backdrop-filter:blur(6px);
                  display:flex; align-items:center; justify-content:center; padding:24px; }
    .fv-overlay-inner { width:min(1400px,100%); height:min(900px,100%);
                        background:#08080d; border:1px solid rgba(255,255,255,.12);
                        border-radius:12px; overflow:hidden;
                        box-shadow:0 30px 80px rgba(0,0,0,.7); }
  `;
  document.head.appendChild(s);
}

async function jget(path) {
  const r = await api.fetchApi(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function jpost(path, body) {
  const r = await api.fetchApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function iframeUrl(st) {
  if (!st || !st.running || !st.port) return null;
  const cfgHost = (st.config && st.config.host) || "";
  const explicit = cfgHost && cfgHost !== "0.0.0.0" &&
                   cfgHost !== "127.0.0.1" && cfgHost !== "localhost";
  const host = explicit ? cfgHost : location.hostname;
  return `${location.protocol}//${host}:${st.port}/`;
}

function buildPanel(container) {
  if (container._fvTimer) { clearInterval(container._fvTimer); container._fvTimer = null; }
  container.innerHTML = "";

  const wrap = document.createElement("div");
  wrap.className = "fv-wrap";
  wrap.innerHTML = `
    <div class="fv-bar">
      <span class="fv-dot"></span>
      <span class="fv-status">Checking…</span>
      <span class="fv-url"></span>
      <span class="fv-spacer"></span>
      <button class="fv-btn" data-act="settings">⚙</button>
      <button class="fv-btn" data-act="start">▶ Start</button>
      <button class="fv-btn" data-act="stop">■ Stop</button>
      <button class="fv-btn" data-act="reload">↻</button>
      <button class="fv-btn" data-act="popout">⤢</button>
    </div>
    <div class="fv-settings" hidden>
      <label>Host<input name="host" type="text"></label>
      <label>Port<input name="port" type="number" min="1" max="65535"></label>
      <label>Keep<input name="keep_history" type="number" min="1" max="1000"></label>
      <button class="fv-btn primary" data-act="save">Save &amp; Restart</button>
    </div>
    <div class="fv-body">
      <iframe class="fv-frame" src="about:blank" referrerpolicy="no-referrer"></iframe>
    </div>
  `;
  container.appendChild(wrap);

  const $ = sel => wrap.querySelector(sel);
  const dot = $(".fv-dot");
  const statusEl = $(".fv-status");
  const urlEl = $(".fv-url");
  const frame = $(".fv-frame");
  const body = $(".fv-body");
  const settings = $(".fv-settings");
  const inHost = $('input[name="host"]');
  const inPort = $('input[name="port"]');
  const inKeep = $('input[name="keep_history"]');

  let lastUrl = null;
  let busy = false;

  function showEmpty(text) {
    if (body.querySelector(".fv-empty")) return;
    const e = document.createElement("div");
    e.className = "fv-empty";
    e.innerHTML = text;
    body.appendChild(e);
  }
  function clearEmpty() {
    const e = body.querySelector(".fv-empty");
    if (e) e.remove();
  }

  async function refresh() {
    try {
      const st = await jget("/flask_viewer/status");
      dot.classList.toggle("on", !!st.running);
      statusEl.textContent = st.running ? "Live" : "Stopped";
      urlEl.textContent = st.running ? st.url : "";
      if (document.activeElement !== inHost) inHost.value = st.config.host;
      if (document.activeElement !== inPort) inPort.value = st.config.port;
      if (document.activeElement !== inKeep) inKeep.value = st.config.keep_history;

      const u = iframeUrl(st);
      if (u && u !== lastUrl) {
        clearEmpty();
        frame.src = u;
        lastUrl = u;
      } else if (!u) {
        frame.src = "about:blank";
        lastUrl = null;
        showEmpty("Server is not running.<br>Click <b>Start</b> or run the Flask Image Viewer node.");
      }
    } catch (e) {
      dot.classList.remove("on");
      statusEl.textContent = "Error";
      showEmpty("Cannot reach ComfyUI control endpoint.<br><small>" + String(e) + "</small>");
    }
  }

  wrap.addEventListener("click", async ev => {
    const btn = ev.target.closest(".fv-btn");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "settings") { settings.hidden = !settings.hidden; return; }
    if (act === "popout") { openOverlay(); return; }
    if (busy) return;
    busy = true;
    try {
      if (act === "start") await jpost("/flask_viewer/start");
      else if (act === "stop") await jpost("/flask_viewer/stop");
      else if (act === "reload") { if (lastUrl) frame.src = lastUrl; }
      else if (act === "save") {
        await jpost("/flask_viewer/config", {
          host: inHost.value.trim(),
          port: Number(inPort.value),
          keep_history: Number(inKeep.value),
        });
        statusEl.textContent = "Restarting…";
        await jpost("/flask_viewer/restart");
      }
      await new Promise(r => setTimeout(r, 500));
      await refresh();
    } catch (e) {
      statusEl.textContent = "Error";
      console.warn("[FlaskViewer]", e);
    } finally {
      busy = false;
    }
  });

  refresh();
  container._fvTimer = setInterval(refresh, 3000);
}

function openOverlay() {
  if (document.querySelector(".fv-overlay")) return;
  const ov = document.createElement("div");
  ov.className = "fv-overlay";
  const inner = document.createElement("div");
  inner.className = "fv-overlay-inner";
  ov.appendChild(inner);
  document.body.appendChild(ov);

  const close = () => {
    if (inner._fvTimer) clearInterval(inner._fvTimer);
    ov.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = e => { if (e.key === "Escape") close(); };
  ov.addEventListener("mousedown", e => { if (e.target === ov) close(); });
  document.addEventListener("keydown", onKey);

  buildPanel(inner);
}

async function tryRegisterSidebar() {
  for (let i = 0; i < 25; i++) {
    const em = app && app.extensionManager;
    if (em && typeof em.registerSidebarTab === "function") {
      try {
        em.registerSidebarTab({
          id: "flask-viewer",
          icon: "pi pi-image",
          title: "Flask Viewer",
          tooltip: "Live image gallery",
          type: "custom",
          render: el => {
            const host = document.createElement("div");
            host.className = "fv-sidebar";
            host.style.height = "100%";
            el.appendChild(host);
            buildPanel(host);
          },
        });
        return true;
      } catch (e) {
        console.warn("[FlaskViewer] sidebar registration failed", e);
        return false;
      }
    }
    await new Promise(r => setTimeout(r, 200));
  }
  return false;
}

function addFloatingButton() {
  if (document.getElementById("fv-fab")) return;
  const btn = document.createElement("button");
  btn.id = "fv-fab";
  btn.className = "fv-btn primary";
  btn.textContent = "◈ Flask Viewer";
  btn.style.cssText =
    "position:fixed;right:16px;bottom:16px;z-index:9000;" +
    "padding:10px 14px;border-radius:10px;font-size:12px;" +
    "box-shadow:0 8px 30px rgba(139,92,246,.4);";
  btn.onclick = openOverlay;
  document.body.appendChild(btn);
}

app.registerExtension({
  name: "FlaskViewer.Panel",
  async setup() {
    injectStyles();
    const ok = await tryRegisterSidebar();
    if (!ok) addFloatingButton();
  },
});