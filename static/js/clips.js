window.og = window.og || {};

// ── Clips: descarga de un rango [start, end] del video ────────────
// El slider de doble handle y los inputs MM:SS comparten una única fuente
// de verdad en SEGUNDOS (clipStart/clipEnd); los textos son proyecciones.
// Invariante: 0 <= clipStart < clipEnd <= clipDuration, con gap mínimo de 1s.
window.og.clipEnabled = false;
window.og.clipStart = 0;
window.og.clipEnd = 0;
window.og.clipDuration = 0;   // duration del video, viene de /api/info
window.og.clipStartText = "0:00";
window.og.clipEndText = "0:00";
window.og.clipStartInvalid = false;
window.og.clipEndInvalid = false;

const CLIP_MIN_GAP = 1; // segundos

// "SS" | "M:SS" | "MM:SS" | "H:MM:SS" -> segundos, o null si no parsea.
window.og.tsToSeconds = function(str) {
  const s = String(str || "").trim();
  if (!s) return null;
  if (!/^\d+(:\d{1,2}){0,2}$/.test(s)) return null;
  const parts = s.split(":").map(Number);
  if (parts.slice(1).some(p => p > 59)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
};

// segundos -> "M:SS" o "H:MM:SS" (para inputs y badges; el filename usa
// puntos vía _fmt_ts en el backend, acá los ':' son solo presentación).
window.og.secondsToTs = function(secs) {
  const total = Math.max(0, Math.floor(secs || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
};

// Reset al inspeccionar una URL nueva (lo llama inspector.js/resetUI).
window.og.clipReset = function(duration) {
  this.clipEnabled = false;
  this.clipDuration = Math.floor(duration || 0);
  this.clipStart = 0;
  this.clipEnd = this.clipDuration;
  this.clipStartInvalid = false;
  this.clipEndInvalid = false;
  this.clipRefreshText();
};

window.og.clipRefreshText = function() {
  this.clipStartText = this.secondsToTs(this.clipStart);
  this.clipEndText = this.secondsToTs(this.clipEnd);
};

// Handles del slider: clamp que empuja sin cruzar (gap mínimo 1s).
window.og.clipSyncFromSlider = function(which) {
  if (which === "start") {
    this.clipStart = Math.min(Number(this.clipStart),
                              this.clipEnd - CLIP_MIN_GAP);
    this.clipStart = Math.max(0, this.clipStart);
  } else {
    this.clipEnd = Math.max(Number(this.clipEnd),
                            this.clipStart + CLIP_MIN_GAP);
    this.clipEnd = Math.min(this.clipDuration, this.clipEnd);
  }
  this.clipStartInvalid = false;
  this.clipEndInvalid = false;
  this.clipRefreshText();
};

// Inputs de texto: parse tolerante; inválido marca el input sin mover nada.
window.og.clipSyncFromText = function(which) {
  const raw = which === "start" ? this.clipStartText : this.clipEndText;
  const secs = this.tsToSeconds(raw);
  if (secs === null || secs > this.clipDuration) {
    if (which === "start") this.clipStartInvalid = true;
    else this.clipEndInvalid = true;
    return;
  }
  if (which === "start") {
    this.clipStart = Math.min(secs, this.clipEnd - CLIP_MIN_GAP);
    this.clipStart = Math.max(0, this.clipStart);
    this.clipStartInvalid = false;
  } else {
    this.clipEnd = Math.max(secs, this.clipStart + CLIP_MIN_GAP);
    this.clipEnd = Math.min(this.clipDuration, this.clipEnd);
    this.clipEndInvalid = false;
  }
  this.clipRefreshText();
};

window.og.clipValid = function() {
  return this.clipEnabled
    && this.clipDuration > 0
    && this.clipStart >= 0
    && this.clipEnd - this.clipStart >= CLIP_MIN_GAP
    && this.clipEnd <= this.clipDuration;
};

// Track del slider: gris -> ámbar -> gris según el rango elegido.
window.og.clipTrackStyle = function() {
  if (!this.clipDuration) return "";
  const a = (this.clipStart / this.clipDuration) * 100;
  const b = (this.clipEnd / this.clipDuration) * 100;
  return `background:linear-gradient(to right,` +
    `var(--line-2) ${a}%, var(--amber) ${a}%,` +
    ` var(--amber) ${b}%, var(--line-2) ${b}%)`;
};

// Cuando ambos handles quedan pegados al final, el de start debe ganar el
// click (si no, el de end lo tapa y el rango queda inutilizable).
window.og.clipStartZ = function() {
  if (!this.clipDuration) return 3;
  return this.clipStart > this.clipDuration * 0.95 ? 5 : 3;
};

// Duración del clip para el label ("Duración del clip: 1:15").
window.og.clipLength = function() {
  return this.secondsToTs(Math.max(0, this.clipEnd - this.clipStart));
};

// Badge para cards/filas de sesión: "1:30–2:45" desde el job serializado.
window.og.clipBadge = function(job) {
  if (!job || job.clip_start == null || job.clip_end == null) return "";
  return this.secondsToTs(job.clip_start) + "–" + this.secondsToTs(job.clip_end);
};
