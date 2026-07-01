window.og = window.og || {};

// ── Storage ───────────────────────────────
window.og.storageOpen = false;
window.og.storageLoaded = false;
window.og.storage = { total_usage_bytes: 0, workdirs: [], loose_files: [], db_size_bytes: 0 };
window.og.storageMsg = "";

window.og.toggleStorage = async function() {
  this.storageOpen = !this.storageOpen;
  if (this.storageOpen && !this.storageLoaded) {
    await this.loadStorage();
  }
};

window.og.storageBadge = function() {
  if (this.storageOpen || !this.storageLoaded) return "";
  const dirs = (this.storage.workdirs || []).length;
  return dirs > 0 ? " · " + dirs + " dirs" : "";
};

window.og.loadStorage = async function() {
  try {
    const r = await fetch("/api/storage");
    this.storage = await r.json();
  } catch (e) {
    this.storage = { total_usage_bytes: 0, workdirs: [], loose_files: [], db_size_bytes: 0 };
  }
  this.storageLoaded = true;
};

window.og.cleanupStorage = async function() {
  if (!confirm(this.t('ui.confirm_cleanup'))) return;
  this.storageMsg = this.t('ui.cleaning');
  try {
    const r = await fetch("/api/storage/cleanup", { method: "POST" });
    const d = await r.json();
    this.storageMsg = this.t('ui.cleanup_success', {count: d.cleaned, bytes: this.formatMB(d.freed_bytes)});
    await this.loadStorage();
  } catch (e) {
    this.storageMsg = this.t('ui.error_cleanup');
  }
};

window.og.cleanupAllStorage = async function() {
  if (!confirm(this.t('ui.confirm_cleanup_all'))) return;
  this.storageMsg = this.t('ui.deleting');
  try {
    const r = await fetch("/api/storage/cleanup-all", { method: "POST" });
    const d = await r.json();
    this.storageMsg = this.t('ui.cleanup_all_success', {count: d.cleaned, bytes: this.formatMB(d.freed_bytes)});
    await this.loadStorage();
  } catch (e) {
    this.storageMsg = this.t('ui.error_delete_workdirs');
  }
};
