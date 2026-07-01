window.og = window.og || {};

// ── Auth ──────────────────────────────────
window.og.token = "";
window.og.authLoading = false;
window.og.authenticated = false;
window.og.authChecked = false;

window.og.doAuth = async function() {
  if (!this.token.trim()) return;
  this.authLoading = true;
  const r = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: this.token.trim() }),
  });
  if (r.ok) {
    this.authenticated = true;
    await this.rehydrate();
    await this.fetchDefaults();
  } else {
    this.errorMsg = this.t('error.token_invalid');
  }
  this.authLoading = false;
};

window.og.init = async function() {
  this.initTheme();
  await this.loadI18n(navigator.language.startsWith('en') ? 'en' : 'es');
  if (!this.AUTH_REQUIRED) {
    this.authenticated = true; this.authChecked = true;
    await this.rehydrate();
    await this.fetchDefaults();
    return;
  }
  try {
    const r = await fetch("/api/history?limit=1");
    this.authenticated = r.status !== 401;
  } catch (e) {
    this.authenticated = false;
  }
  this.authChecked = true;
  if (this.authenticated) {
    await this.rehydrate();
    await this.fetchDefaults();
  }
};
