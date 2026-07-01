window.og = window.og || {};

// ── Quality ───────────────────────────────
window.og.quality = "best";
window.og.subsEnabled = false;
window.og.thumbEnabled = false;
window.og.infojsonEnabled = false;
window.og.incognitoEnabled = false;
window.og.qualities = [
  { id: "best",  label: "best mp4" },
  { id: "1080p", label: "1080p" },
  { id: "720p",  label: "720p" },
  { id: "480p",  label: "480p" },
  { id: "audio", label: "solo audio · mp3" },
];

// ── Engine (yt-dlp hot-swap) ──────────────
window.og.engineUpdating = false;
window.og.engineMsg = "";

// ── Download ──────────────────────────────
window.og.downloading = false;
window.og.terminal = [];
window.og.dlSpeed = "";
window.og.dlEta = "";
window.og.showTerminal = false;
window.og.progress = 0;
window.og.progressDone = false;
window.og.downloadUrl = "";
window.og.downloadFilename = "";
window.og.successPath = "";
window.og.lastJobId = "";
window.og.savingTo = false;
window.og.lastSaveDir = "";

// ── Download function ─────────────────────
window.og.download = async function() {
  if (!this.url.trim()) return;
  // Reset solo de la vista enfocada (no toca otras tarjetas en curso).
  this.resetFocusView();

  // Modo incógnito: el archivo NO va al historial ni a la carpeta default;
  // se entrega a una carpeta elegida por el usuario. La pedimos ANTES de
  // crear el job (mismo picker que "Guardar en…").
  let incognitoDir = null;
  if (this.incognitoEnabled) {
    if (window.pywebview?.api?.pick_folder) {
      try { incognitoDir = await window.pywebview.api.pick_folder(); }
      catch (e) { incognitoDir = null; }
      if (!incognitoDir) return;  // usuario canceló
    } else {
      incognitoDir = window.prompt(this.t('ui.incognito_pick_dir'),
                                   this.libraryDirDefault());
      if (!incognitoDir || !incognitoDir.trim()) return;
      incognitoDir = incognitoDir.trim();
    }
  }

  let jobId, title = this.title !== "—" ? this.title : this.url.trim();
  try {
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        url: this.url.trim(), quality: this.quality,
        subs: this.subsEnabled, thumb: this.thumbEnabled,
        infojson: this.infojsonEnabled,
        incognito: this.incognitoEnabled,
        incognito_dir: incognitoDir,
      }),
    });
    if (!r.ok) throw new Error(((await r.json()).detail || this.t('ui.error_create_job')) + " (HTTP " + r.status + ")");
    ({ job_id: jobId } = await r.json());
  } catch (err) {
    this.errorMsg = err.message;
    this.canRetry = true;
    this.downloading = false;
    console.error("download failed:", err);
    return;
  }

  // Alta inmediata de la tarjeta; queda enfocada en la terminal.
  this.upsertJob({
    id: jobId, status: "queued", percent: 0, speed: "", eta: "",
    note: "", title, filename: "", filepath: "", error: "",
    created: Date.now() / 1000, finished: 0,
    incognito: this.incognitoEnabled,
  });
  this.focusJob(jobId);
  this.jobLine(jobId, "$", "yt-dlp -f " + this.quality + " " + this.url.trim());
  this.attachStream(jobId, true);
};

window.og.waitJob = async function(jobId, title) {
  return new Promise((resolve) => {
    const ev = new EventSource("/api/jobs/" + jobId + "/events");
    let lastLiveIdx = -1;
    let retries = 0;
    const timeout = setTimeout(() => {
      this.addLine("✗", this.t('download.timeout_title', {title: title}), "err");
      ev.close(); resolve();
    }, 600000);
    ev.onmessage = (m) => {
      const s = JSON.parse(m.data);
      if (s.status === "downloading" && s.speed) {
        const txt = "  " + (s.percent || 0).toFixed(1) + "%  " + (s.speed || "") + (s.eta ? "  ETA " + s.eta : "");
        if (lastLiveIdx >= 0) {
          this.terminal[lastLiveIdx] = { pfx: "›", text: txt, cls: "", live: true };
          this.terminal = [...this.terminal];
        } else {
          const idx = this.terminal.length;
          this.terminal = [...this.terminal, { pfx: "›", text: txt, cls: "", live: true }];
          lastLiveIdx = idx;
        }
      }
      if (s.status === "done") { clearTimeout(timeout); this.addLine("✓", this.t('download.ok_filename', {filename: title}), "ok"); ev.close(); resolve(); }
      if (s.status === "error") { clearTimeout(timeout); this.addLine("✗", this.t('download.error_title', {title: s.error || this.t('ui.unknown')}), "err"); ev.close(); resolve(); }
    };
    ev.onerror = () => {
      if (retries < 3 && ev.readyState === EventSource.CONNECTING) { retries++; return; }
      clearTimeout(timeout); ev.close(); resolve();
    };
  });
};

// ── Engine update ─────────────────────────
window.og.updateEngine = async function() {
  if (this.engineUpdating) return;
  this.engineUpdating = true;
  this.engineMsg = this.t('ui.updating_engine');
  try {
    const r = await fetch("/api/engine/update", { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || this.t('ui.engine_update_failed_short'));
    this.engineMsg = d.updated
      ? this.t('ui.engine_updated', {version: d.version})
      : this.t('ui.engine_latest');
  } catch (err) {
    this.engineMsg = this.t('ui.engine_update_error', {msg: err.message});
  } finally {
    this.engineUpdating = false;
  }
};

// ── Mirror to terminal ───────────────────
window.og.mirrorToTerminal = function(jobId) {
  const j = this.sessionJobs[jobId];
  if (!j || this.focusedJobId !== jobId) return;
  const active = ["queued", "starting", "downloading", "processing"].includes(j.status);
  this.downloading = active;
  this.progress = j.percent || 0;
  this.dlSpeed = j.status === "downloading" ? (j.speed || "") : "";
  this.dlEta   = j.status === "downloading" ? (j.eta || "")   : "";
  this.progressDone = j.status === "done";
  if (j.status === "done") {
    this.progress = 100;
    if (j.incognito) {
      // Incógnito: el archivo se entregó a la carpeta elegida y no se
      // sirve por HTTP (vive fuera de los allowed roots). Sin link.
      this.downloadUrl = "";
      this.downloadFilename = "";
      this.successPath = this.t('ui.incognito_delivered');
    } else {
      this.downloadUrl = "/api/jobs/" + jobId + "/file";
      this.downloadFilename = j.filename || "";
      this.successPath = j.filepath || j.filename || "";
    }
    this.lastJobId = jobId;
    this.errorMsg = ""; this.canRetry = false;
  } else if (j.status === "error") {
    this.errorMsg = j.error || this.t('ui.error_download_failed');
    this.canRetry = true;
    this.downloadUrl = ""; this.successPath = "";
  } else {
    this.downloadUrl = ""; this.successPath = "";
    this.errorMsg = ""; this.canRetry = false;
  }
};

// ── Cancel job ────────────────────────────
window.og.cancelJob = async function(jobId) {
  try {
    const r = await fetch("/api/jobs/" + jobId + "/cancel", { method: "POST" });
    if (!r.ok) return;
    const d = await r.json();
    // "cancelled": estaba sólo en cola (sin thread ni SSE) -> actualizar acá.
    // "cancelling": corriendo -> el SSE entregueará el estado 'cancelled'.
    if (d.status === "cancelled" && this.sessionJobs[jobId]) {
      this.sessionJobs[jobId].status = "cancelled";
      this.sessionJobs[jobId].finished = Date.now() / 1000;
      this.closeStream(jobId);
      if (this.focusedJobId === jobId) this.mirrorToTerminal(jobId);
    }
  } catch (e) { /* noop */ }
};

// ── Reset Focus View ─────────────────────
window.og.resetFocusView = function() {
  this.focusedJobId = "";
  this.terminal = [];
  this.dlSpeed = "";
  this.dlEta = "";
  this.showTerminal = false;
  this.progress = 0;
  this.progressDone = false;
  this.downloadUrl = "";
  this.downloadFilename = "";
  this.successPath = "";
  this.lastJobId = "";
  this.errorMsg = "";
  this.canRetry = false;
};

// ── Reset UI ──────────────────────────────
window.og.resetUI = function() {
  this.resetFocusView();
  this.isPlaylist = false;
  this.playlistAvailable = false;
  this.playlistVideos = [];
  this.formats = [];
  this.showFormats = false;
  // Clear batch state
  if (this.batchPollTimer) {
    clearInterval(this.batchPollTimer);
    this.batchPollTimer = null;
  }
  this.batchJobIds = [];
  this.batchJobs = {};
  this.batchDone = false;
  this.batchStartTime = null;
};

// ── Folder operations ─────────────────────
window.og.openFolder = async function(jobId) {
  if (!jobId) return;
  await fetch("/api/jobs/" + jobId + "/open-folder", { method: "POST" });
};

window.og.openDownloadsFolder = async function() {
  await fetch("/api/open-downloads-folder", { method: "POST" });
};

// Busca el library_dir configurado para sugerirlo como default del prompt.
window.og.libraryDirDefault = function() {
  if (this.lastSaveDir) return this.lastSaveDir;
  const s = (this.settings || []).find(x => x.key === "library_dir");
  return (s && s.value) || "";
};

// "Guardar en…": mueve el archivo (server-side) a la carpeta elegida.
// Prioriza el picker nativo de WebView2; si no existe (browser / red),
// cae a pedir la ruta del servidor por prompt.
window.og.saveTo = async function(jobId) {
  if (!jobId || this.savingTo) return;
  let dest = null;
  if (window.pywebview?.api?.pick_folder) {
    try { dest = await window.pywebview.api.pick_folder(); }
    catch (e) { dest = null; }
    if (!dest) return;  // usuario canceló
  } else {
    dest = window.prompt(this.t('ui.save_as'),
                         this.libraryDirDefault());
    if (!dest || !dest.trim()) return;
    dest = dest.trim();
  }
  this.savingTo = true;
  try {
    const r = await fetch("/api/jobs/" + jobId + "/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dest }),
    });
    if (!r.ok) {
      let msg = this.t('ui.error_save_file');
      try { const e = await r.json(); if (e.detail) msg = e.detail; } catch (_) {}
      this.errorMsg = msg;
      return;
    }
    const d = await r.json();
    this.lastSaveDir = dest;
    this.successPath = d.filepath || this.successPath;
    // Mantener funcionando "Abrir carpeta" y el link de descarga.
    if (this.sessionJobs[jobId]) this.sessionJobs[jobId].filepath = d.filepath;
    this.errorMsg = "";
  } catch (e) {
    this.errorMsg = this.t('ui.error_save_file');
  } finally {
    this.savingTo = false;
  }
};
