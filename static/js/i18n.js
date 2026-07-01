window.og = window.og || {};

// ── i18n ─────────────────────────────────
window.og.i18n = {};
window.og.lang = "es";

window.og.loadI18n = async function(lang) {
  try {
    const r = await fetch(`/static/i18n/${lang}.json`);
    if (r.ok) {
      this.i18n = await r.json();
      this.lang = lang;
    } else if (lang !== "en") {
      await this.loadI18n("en");
    }
  } catch (e) {
    if (lang !== "en") await this.loadI18n("en");
  }
  document.documentElement.lang = this.lang;
  document.cookie = `opengrab_lang=${this.lang};path=/;SameSite=Lax`;
};

window.og.t = function(key, ...args) {
  let text = this.i18n[key];
  if (text === undefined) return key;
  if (args.length === 1 && typeof args[0] === 'object' && args[0] !== null) {
    const obj = args[0];
    for (const [k, v] of Object.entries(obj)) {
      text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
    }
  } else {
    args.forEach((a, i) => {
      text = text.replace(new RegExp(`\\{${i}\\}`, 'g'), String(a));
    });
  }
  return text;
};

window.og.reloadI18n = async function(lang) {
  await this.loadI18n(lang);
  this.i18n = { ...this.i18n };  // trigger reactivity
};
