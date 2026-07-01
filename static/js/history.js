window.og = window.og || {};

// ── History ───────────────────────────────
window.og.historyOpen = false;
window.og.historyLoaded = false;
window.og.history = [];
window.og.historyFilter = "";
window.og.pendingDeletes = {};      // { jobId: { timeoutId, entry } }
window.og._clearAllPending = null;  // { timeoutId, history }
window.og.toast = { show: false, message: "", type: null };  // 'pending' | 'success'

window.og.loadHistory = async function() {
  try {
    const r = await fetch("/api/history?limit=15");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    // Defensa: no revivir items con borrado diferido en vuelo.
    this.history = data.filter(e => !(e.job_id in this.pendingDeletes));
  } catch (e) {
    this.history = [];
  }
  this.historyLoaded = true;
};

window.og.toggleHistory = async function() {
  this.historyOpen = !this.historyOpen;
  if (this.historyOpen && !this.historyLoaded) {
    await this.loadHistory();
  }
};

window.og.historyBadge = function() {
  if (this.historyOpen || !this.history.length) return "";
  return " · " + this.history.length;
};

// ── Deferred destruction (patron "undo send") ──
// El click NO toca el backend: saca el item, arranca un timer de 5s y
// muestra el toast. Deshacer cancela el timer (nada se borro). Si el timer
// vence, recien ahi se dispara el DELETE real (DB + secure-delete del archivo,
// irreversible). "No confirmado = no destruido".
window.og.deleteHistoryEntry = function(jobId) {
  const entry = this.history.find(e => e.job_id === jobId);
  if (!entry) return;
  this._removeWithFade(jobId);
  const timeoutId = setTimeout(() => this._executeDelete(jobId), 5000);
  this.pendingDeletes[jobId] = { timeoutId, entry };
  this._updateToast();
};

window.og._removeWithFade = function(jobId) {
  const drop = () => { this.history = this.history.filter(e => e.job_id !== jobId); };
  const el = document.querySelector('.h-entry[data-job-id="' + jobId + '"]');
  if (el) {
    el.classList.add("removing");
    setTimeout(drop, 300);  // espera la transicion CSS antes del splice
  } else {
    drop();  // degradacion elegante si no se encuentra el nodo
  }
};

window.og._executeDelete = async function(jobId) {
  const pending = this.pendingDeletes[jobId];
  if (!pending) return;
  try {
    const r = await fetch("/api/history/" + jobId, { method: "DELETE" });
    if (!r.ok) {
      let detail = this.t('ui.error_server');
      try { detail = (await r.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    // Exito: si no quedan mas pendientes, success toast; si quedan, refrescar cuenta
    delete this.pendingDeletes[jobId];
    if (Object.keys(this.pendingDeletes).length || this._clearAllPending) {
      this._updateToast();
    } else {
      this.toast = { show: true, message: this.t('ui.deleted'), type: 'success' };
      setTimeout(() => { if (this.toast.type === 'success') this.dismissToast(); }, 3000);
    }
  } catch (e) {
    // El DELETE falló: el archivo sigue intacto, restauramos el item.
    this.history.push(pending.entry);
    this.history.sort((a, b) => (b.completed || 0) - (a.completed || 0));
    this.errorMsg = this.t('ui.error_delete_entry', {msg: e.message});
    delete this.pendingDeletes[jobId];
    this._updateToast();
  }
};

window.og.undoDelete = function() {
  // Cancela todos los timers en vuelo (individuales y clear-all).
  Object.values(this.pendingDeletes).forEach(p => clearTimeout(p.timeoutId));
  if (this._clearAllPending) clearTimeout(this._clearAllPending.timeoutId);
  // Reconstruye la lista. clear-all e individuales son mutuamente excluyentes:
  // clearAllHistory absorbe los pendientes individuales en su snapshot.
  if (this._clearAllPending) {
    this.history = this._clearAllPending.history;
  } else {
    Object.values(this.pendingDeletes).forEach(p => this.history.push(p.entry));
    this.history.sort((a, b) => (b.completed || 0) - (a.completed || 0));
  }
  this.pendingDeletes = {};
  this._clearAllPending = null;
  this.toast = { show: false, message: "", type: null };
};

window.og._updateToast = function() {
  const n = Object.keys(this.pendingDeletes).length;
  if (this._clearAllPending) {
    this.toast = { show: true, message: this.t('ui.history_cleared'), type: 'pending' };
  } else if (n > 0) {
    this.toast = { show: true, message: this.t('ui.history_cleared_count', {count: n}), type: 'pending' };
  } else {
    this.toast = { show: false, message: "", type: null };
  }
};

window.og.dismissToast = function() {
  this.toast = { show: false, message: "", type: null };
};

window.og.acceptDeletes = function() {
  const ids = Object.keys(this.pendingDeletes);
  const hasClearAll = !!this._clearAllPending;
  Object.values(this.pendingDeletes).forEach(p => clearTimeout(p.timeoutId));
  if (this._clearAllPending) clearTimeout(this._clearAllPending.timeoutId);
  this.toast = { show: true, message: this.t('ui.deleting_history'), type: 'pending' };
  for (const jobId of ids) this._executeDelete(jobId);
  if (hasClearAll) this._executeClearAll();
};

window.og.clearAllHistory = function() {
  if (!this.history.length && !Object.keys(this.pendingDeletes).length) return;
  // Absorbe los borrados individuales pendientes: cancela sus timers y suma
  // sus entries al snapshot, asi "Deshacer" restaura TODO de una.
  Object.values(this.pendingDeletes).forEach(p => clearTimeout(p.timeoutId));
  const snapshot = [
    ...this.history,
    ...Object.values(this.pendingDeletes).map(p => p.entry),
  ].sort((a, b) => (b.completed || 0) - (a.completed || 0));
  this.pendingDeletes = {};
  if (this._clearAllPending) clearTimeout(this._clearAllPending.timeoutId);
  this.history = [];  // optimistic
  const timeoutId = setTimeout(() => this._executeClearAll(), 5000);
  this._clearAllPending = { timeoutId, history: snapshot };
  this._updateToast();
};

window.og._executeClearAll = async function() {
  const pending = this._clearAllPending;
  if (!pending) return;
  try {
    const r = await fetch("/api/history", { method: "DELETE" });
    if (!r.ok) {
      let detail = this.t('ui.error_server');
      try { detail = (await r.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    this._clearAllPending = null;
    this.toast = { show: true, message: this.t('ui.history_deleted'), type: 'success' };
    setTimeout(() => { if (this.toast.type === 'success') this.dismissToast(); }, 3000);
  } catch (e) {
    this.history = pending.history;
    this.errorMsg = this.t('ui.error_clear_history', {msg: e.message});
    this._clearAllPending = null;
    this._updateToast();
  }
};
