window.og = window.og || {};

// ── Sesión: descargas concurrentes (tarjetas) ──
window.og.sessionJobs = {};     // {id: {id,status,percent,speed,eta,note,title,filename,filepath,error,created,finished,lines}}
window.og.focusedJobId = "";   // job cuyo detalle se ve en la terminal
window.og.jobExtras = {};      // {jobId: [{filename, type, size}, ...]}
window.og._streams = {};        // {id: EventSource} para no duplicar conexiones

// ── Error ─────────────────────────────────
window.og.errorMsg = "";
window.og.canRetry = false;

// ── Computed getters ─────────────────────
// Jobs vivos ordenados: activos primero (por created asc), luego
// terminados (por finished desc). Alimenta la lista de tarjetas.
Object.defineProperty(window.og, 'sortedSessionJobs', {
  get: function() {
    const active = [], done = [];
    const ACT = ["queued", "starting", "downloading", "processing"];
    for (const j of Object.values(this.sessionJobs)) {
      (ACT.includes(j.status) ? active : done).push(j);
    }
    active.sort((a, b) => (a.created || 0) - (b.created || 0));
    done.sort((a, b) => (b.finished || 0) - (a.finished || 0));
    return [...active, ...done];
  }
});

Object.defineProperty(window.og, 'sessionActiveCount', {
  get: function() {
    const ACT = ["queued", "starting", "downloading", "processing"];
    return Object.values(this.sessionJobs).filter(j => ACT.includes(j.status)).length;
  }
});

// Historial sin los jobs que ahora se muestran como tarjeta (evita duplicar:
Object.defineProperty(window.og, 'visibleHistory', {
  get: function() {
    const filtered = this.history.filter(e => !(e.job_id in this.sessionJobs));
    const q = (this.historyFilter || "").toLowerCase().trim();
    if (!q) return filtered;
    return filtered.filter(h =>
      (h.title || "").toLowerCase().includes(q) ||
      (h.url || "").toLowerCase().includes(q) ||
      (h.extractor || "").toLowerCase().includes(q)
    );
  }
});

// ── Job operations ────────────────────────
// Inserta/actualiza un job en el mapa preservando sus líneas de log.
window.og.upsertJob = function(snap) {
  const prev = this.sessionJobs[snap.id] || { lines: [] };
  this.sessionJobs[snap.id] = { ...prev, ...snap, lines: prev.lines };
};

// Rehidrata el estado desde el server (al cargar / reabrir desde el tray).
window.og.rehydrate = async function() {
  let jobs;
  try {
    const r = await fetch("/api/jobs", { credentials: "include" });
    if (!r.ok) return;
    jobs = await r.json();
  } catch (e) { return; }

  const ACT = ["queued", "starting", "downloading", "processing"];
  for (const j of jobs) {
    this.upsertJob({
      id: j.id, status: j.status, percent: j.percent || 0,
      speed: j.speed || "", eta: j.eta || "", note: j.note || "",
      title: j.title || "", filename: j.filename || "",
      filepath: j.filepath || "", error: j.error || "",
      created: j.created || 0, finished: j.finished || 0,
      incognito: !!j.incognito,
    });
    if (ACT.includes(j.status)) this.attachStream(j.id, false);
  }
  // Enfocar el activo más reciente para que la terminal muestre algo.
  const actives = this.sortedSessionJobs.filter(j => ACT.includes(j.status));
  if (actives.length && !this.focusedJobId) {
    this.focusJob(actives[actives.length - 1].id);
  }
};

// Abre (o reusa) el SSE de un job. Alimenta su tarjeta siempre; si está
// en foco, espeja a la terminal escalar (preserva la UI actual).
window.og.attachStream = function(jobId, focus = true) {
  if (focus) this.focusJob(jobId);
  if (this._streams[jobId]) return;  // ya conectado

  const ev = new EventSource("/api/jobs/" + jobId + "/events");
  this._streams[jobId] = ev;
  let lastStatus = (this.sessionJobs[jobId] || {}).status || "";
  let retries = 0;

  ev.onmessage = (m) => {
    const s = JSON.parse(m.data);
    this.upsertJob({ id: jobId, ...s });
    const focused = this.focusedJobId === jobId;

    if (s.status !== lastStatus) {
      lastStatus = s.status;
      if (s.status === "downloading") this.jobLine(jobId, "›", this.t('download.downloading_streams'));
      if (s.status === "processing")  this.jobLine(jobId, "›", s.note || this.t('download.processing'));
    }
    if (focused) this.mirrorToTerminal(jobId);

    if (s.status === "done") {
      this.jobLine(jobId, "✓", this.t('download.done_to', {filename: s.filename || ""}), "ok");
      if (focused) this.mirrorToTerminal(jobId);
      if (this.historyLoaded) this.loadHistory();
      this.closeStream(jobId);
    }
    if (s.status === "error") {
      this.jobLine(jobId, "✗", this.t('download.error_with_msg', {msg: s.error || this.t('ui.unknown')}), "err");
      if (focused) this.mirrorToTerminal(jobId);
      this.closeStream(jobId);
    }
    if (s.status === "cancelled") {
      this.jobLine(jobId, "✕", this.t('download.cancelled'), "err");
      if (focused) this.mirrorToTerminal(jobId);
      this.closeStream(jobId);
    }
  };
  ev.onerror = () => {
    if (retries < 5 && ev.readyState === EventSource.CONNECTING) { retries++; return; }
    this.closeStream(jobId);
  };
};

window.og.closeStream = function(jobId) {
  const ev = this._streams[jobId];
  if (ev) { ev.close(); delete this._streams[jobId]; }
};

window.og.jobLine = function(jobId, pfx, text, cls = "") {
  const j = this.sessionJobs[jobId];
  if (!j) return;
  j.lines = [...(j.lines || []), { pfx, text, cls }];
  if (this.focusedJobId === jobId) this.terminal = j.lines;
};

// Pasa un job a foco: la terminal escalar refleja su estado y su log.
window.og.focusJob = function(jobId) {
  const j = this.sessionJobs[jobId];
  if (!j) return;
  this.focusedJobId = jobId;
  this.terminal = j.lines || [];
  this.mirrorToTerminal(jobId);
  if (j.status === "done" && !this.jobExtras[jobId]) {
    this.fetchJobExtras(jobId);
  }
};

window.og.fetchJobExtras = async function(jobId) {
  try {
    const r = await fetch("/api/jobs/" + jobId + "/extras");
    if (r.ok) {
      this.jobExtras = { ...this.jobExtras, [jobId]: await r.json() };
    }
  } catch (e) { /* no-op */ }
};

window.og.dismissJob = function(jobId) {
  this.closeStream(jobId);
  delete this.sessionJobs[jobId];
  if (this.focusedJobId === jobId) {
    this.focusedJobId = "";
    this.terminal = []; this.downloading = false; this.progress = 0;
    this.progressDone = false; this.downloadUrl = ""; this.successPath = "";
    this.lastJobId = ""; this.errorMsg = ""; this.canRetry = false;
  }
  fetch("/api/jobs/" + jobId + "/dismiss", { method: "POST" }).catch(() => {});
};
