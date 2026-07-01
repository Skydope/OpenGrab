window.og = window.og || {};

// ── UI helpers ────────────────────────────
window.og.fmtSize = function(bytes) {
  if (!bytes || bytes < 1024) return (bytes || 0) + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
};

window.og.fmtDuration = function(secs) {
  if (!secs) return "";
  const d = new Date(secs * 1000).toISOString();
  return d.substring(11, 19).replace(/^00:/, "");
};

window.og.formatMB = function(bytes) { return (bytes / 1048576).toFixed(1) + " MB"; };

window.og.formatAge = function(hours) {
  if (hours < 1) return Math.round(hours * 60) + "min";
  if (hours < 24) return Math.round(hours) + "h";
  return Math.round(hours / 24) + "d";
};

window.og.addLine = function(pfx, text, cls = "") {
  this.terminal = [...this.terminal, { pfx, text, cls }];
};
