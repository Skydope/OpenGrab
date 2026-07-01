window.og = window.og || {};

// ── Theme ─────────────────────────────────
window.og.theme = "dark";

window.og.initTheme = function() {
  const saved = localStorage.getItem("opengrab-theme");
  if (saved) {
    this.theme = saved;
  } else {
    this.theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  this.applyTheme();
};

window.og.toggleTheme = function() {
  this.theme = this.theme === "dark" ? "light" : "dark";
  localStorage.setItem("opengrab-theme", this.theme);
  this.applyTheme();
};

window.og.applyTheme = function() {
  document.documentElement.setAttribute("data-theme", this.theme);
};
