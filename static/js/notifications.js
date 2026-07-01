window.og = window.og || {};

// ── Watch Notifications ──────────────────
window.og._notifPerm = "default";
window.og._watchPollTimer = null;
window.og._lastKnownVideoCount = 0;

window.og.requestNotifPermission = async function() {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted") return "granted";
  const perm = await Notification.requestPermission();
  this._notifPerm = perm;
  return perm;
};

window.og.notify = function(title, body) {
  if (!("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  new Notification(title, {
    body: body,
    icon: "/static/icon-192.png",
    tag: "opengrab-watch",
  });
};

window.og.startWatchPoll = function() {
  if (this._watchPollTimer) return;
  this._watchPollTimer = setInterval(() => { this.pollWatch(); }, 60000);
  this.pollWatch();  // primera consulta inmediata para inicializar el contador
};

window.og.stopWatchPoll = function() {
  if (this._watchPollTimer) {
    clearInterval(this._watchPollTimer);
    this._watchPollTimer = null;
  }
};

window.og.pollWatch = async function() {
  try {
    const r = await fetch("/api/channels");
    if (!r.ok) return;
    const channels = await r.json();
    let totalChecked = 0;
    for (const ch of channels) {
      if (ch.last_checked) {
        const ageSec = (Date.now() / 1000) - ch.last_checked;
        if (ageSec < 120) totalChecked++;
      }
    }
    if (this._lastKnownVideoCount > 0 && totalChecked > this._lastKnownVideoCount) {
      const newVideos = totalChecked - this._lastKnownVideoCount;
      this.notify("OpenGrab", this.t("ui.videos_found", {count: newVideos}));
    }
    this._lastKnownVideoCount = totalChecked;
  } catch (e) { /* silencioso: el poller no debe romper nada */ }
};
