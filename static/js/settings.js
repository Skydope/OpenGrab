window.og = window.og || {};

// ── Settings ──────────────────────────────
window.og.settingsModalOpen = false;
window.og.settingsLoaded = false;
window.og.settingsLoading = false;
window.og.settings = [];
window.og.activeTab = "downloads";
window.og.settingsTabs = [
  { id: "downloads", label: "ui.tab_downloads" },
  { id: "storage", label: "ui.tab_storage" },
  { id: "interface", label: "ui.tab_interface" },
  { id: "advanced", label: "ui.tab_advanced" },
];

window.og.loadSettings = async function() {
  if (this.settingsLoaded) return;
  this.settingsLoading = true;
  const r = await fetch("/api/settings");
  if (r.ok) {
    const data = await r.json();
    this.settings = data.map(s => ({ ...s, _editValue: s.value, _error: null, _saving: false }));
    this.settingsLoaded = true;
  }
  this.settingsLoading = false;
};

window.og.openSettingsModal = async function() {
  this.activeTab = "downloads";
  this.settingsModalOpen = true;
  document.body.classList.add("modal-open");
  await this.loadSettings();
};

window.og.closeSettingsModal = function() {
  this.settingsModalOpen = false;
  document.body.classList.remove("modal-open");
};

window.og.settingsByGroup = function(group) {
  return this.settings.filter(s => (s.group || "advanced") === group);
};

window.og.refreshSettings = function() {
  this.settingsLoaded = false;
  this.settings = [];
  if (this.settingsModalOpen) this.loadSettings();
};

window.og.fetchDefaults = async function() {
  try {
    const r = await fetch("/api/settings/defaults");
    if (!r.ok) return;
    const d = await r.json();
    if (d.quality_default && this.quality === "best") {
      this.quality = d.quality_default;
    }
    if (d.theme && d.theme !== "auto") {
      const local = localStorage.getItem("opengrab-theme");
      if (!local || local === "dark" || local === "light") {
        this.theme = d.theme;
        localStorage.setItem("opengrab-theme", d.theme);
        this.applyTheme();
      }
    }
    if (d.lang && d.lang !== "auto" && d.lang !== this.lang) {
      await this.reloadI18n(d.lang);
    }
    if (d.notifications_enabled) {
      const perm = await this.requestNotifPermission();
      if (perm === "granted") this.startWatchPoll();
    }
    if (d.subs_default) this.subsEnabled = true;
    if (d.thumb_default) this.thumbEnabled = true;
    if (d.infojson_default) this.infojsonEnabled = true;
  } catch (e) { /* no-op */ }
};

window.og.validateAndSave = function(s) {
  s._error = null;
  if (s.locked) return;
  const raw = s._editValue;
  if (s.type === "int") {
    const v = parseInt(String(raw), 10);
    if (isNaN(v) || String(v) !== String(raw).trim()) {
      s._error = this.t('ui.validate_int');
      return;
    }
    if (s.validation) {
      if (s.validation.min !== null && v < s.validation.min) {
        s._error = this.t('ui.validate_min', {min: s.validation.min});
        return;
      }
      if (s.validation.max !== null && v > s.validation.max) {
        s._error = this.t('ui.validate_max', {max: s.validation.max});
        return;
      }
    }
    s._editValue = v;
  }
  if (s.type === "string" && !s.options && String(raw).trim() === "") {
    s._error = this.t('ui.validate_empty');
    return;
  }
  this.saveSetting(s);
};

window.og.saveSetting = async function(s) {
  if (s.locked || s._error) return;
  s._saving = true;
  s._error = null;
  try {
    const body = JSON.stringify({ [s.key]: String(s._editValue) });
    const r = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body,
    });
    const d = await r.json();
    if (r.ok && d.updated && d.updated.includes(s.key)) {
      s.value = s._editValue;
      s.origin = "table";
      s.locked = false;
      this.showToast(this.t("ui.settings_saved", {setting_key: this.t(s.description || s.key)}), "success");
      // Side effects
      if (s.key === "theme") {
        localStorage.setItem("opengrab-theme", s._editValue);
        this.theme = s._editValue;
        this.applyTheme();
      }
      if (s.key === "quality_default") {
        this.quality = s._editValue;
      }
      if (s.key === "lang") {
        await this.reloadI18n(s._editValue);
      }
      if (s.key === "notifications_enabled") {
        const enabled = s._editValue === true || s._editValue === "true";
        if (enabled) {
          const perm = await this.requestNotifPermission();
          if (perm === "granted") {
            this.startWatchPoll();
          } else if (perm === "denied") {
            s._error = this.t('ui.validate_notif_denied');
          }
        } else {
          this.stopWatchPoll();
        }
      }
    } else {
      s._error = (d.errors && d.errors[s.key]) || d.detail || d.error || this.t('ui.error_save_setting');
    }
  } catch (e) {
    s._error = this.t('ui.error_connection');
  } finally {
    s._saving = false;
  }
};

window.og.showToast = function(msg, type) {
  this.toast = { show: true, message: msg, type: type };
  if (this._toastTimer) clearTimeout(this._toastTimer);
  this._toastTimer = setTimeout(() => {
    this.toast.show = false;
  }, 3000);
};

window.og.dismissToast = function() {
  this.toast.show = false;
  if (this._toastTimer) clearTimeout(this._toastTimer);
};

// ── Backup ────────────────────────────────
window.og.exportBackup = async function() {
  try {
    const r = await fetch("/api/backup/export");
    if (!r.ok) return;
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "opengrab-backup-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { /* no-op */ }
};

window.og.importBackup = function() {
  document.getElementById("backup-file")?.click();
};

window.og.handleBackupFile = async function(e) {
  const file = e.target?.files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const s = data.settings ? Object.keys(data.settings).length : 0;
    const h = (data.history || []).length;
    const c = (data.channels || []).length;
    if (!confirm(this.t('ui.confirm_import', {settings: s, history: h, channels: c}))) return;
    const r = await fetch("/api/backup/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await r.json();
    if (r.ok && result.ok) {
      this.showToast(this.t('ui.import_success', result.imported), "success");
      this.refreshSettings();
      if (this.historyOpen) { this.historyLoaded = false; this.history = []; this.toggleHistory(); }
    } else {
      this.showToast(result.detail || "Error al importar", "error");
    }
  } catch (e) {
    this.showToast("JSON inválido", "error");
  }
  e.target.value = "";
};
