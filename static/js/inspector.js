window.og = window.og || {};

// ── Inspect ───────────────────────────────
window.og.url = "";
window.og.inspecting = false;
window.og.meta = false;
window.og.thumbnail = "";
window.og.site = "";
window.og.title = "—";
window.og.subtitle = "—";
window.og.cmdCopied = false;
window.og.formats = [];
window.og.showFormats = false;

window.og.classifyUrl = function(u) {
  const hasV = /[?&]v=/.test(u);
  const hasList = /[?&]list=/.test(u);
  if (hasList && !hasV) return "playlist";
  if (hasV && hasList) return "video+list";

  // Detect non-YouTube playlists by URL pattern
  const lower = u.toLowerCase();
  // SoundCloud sets: /username/sets/set-name
  if (/\/sets\/[^/?#]+/.test(lower)) return "playlist";
  // Vimeo channels/showcases: /channels/name or /showcase/name
  if (/\/(channels|showcase)\/[^/?#]+/.test(lower)) return "playlist";
  // TikTok user pages (not single video): /@username without /video/
  if (/\/@[^/?#]+$/.test(lower) || /\/@[^/?#]+\/?$/.test(lower)) return "playlist";
  // Generic playlist paths: Dailymotion /playlist/..., etc.
  if (/\/playlist\/[^/?#]+/.test(lower)) return "playlist";

  return "video";
};

window.og.inspect = async function() {
  if (!this.url.trim()) { this.errorMsg = this.t('ui.error_url_first'); return; }
  this.resetUI();
  const kind = this.classifyUrl(this.url.trim());

  if (kind === "playlist") {
    this.isPlaylist = true;
    this.meta = false;
    this.inspecting = true;
    await this.loadPlaylist();
    this.inspecting = false;
    return;
  }

  this.inspecting = true;
  try {
    const r = await fetch("/api/info?url=" + encodeURIComponent(this.url.trim()),
      { credentials: "include" });
    if (!r.ok) {
      let msg = this.t('ui.error_analyze');
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg + " (HTTP " + r.status + ")");
    }
    const data = await r.json();
    this.meta = true;
    this.thumbnail = data.thumbnail || "";
    this.site = data.site || "";
    this.title = data.title || "—";
    const views = data.view_count
      ? " · " + Intl.NumberFormat("es-AR").format(data.view_count) + " views"
      : "";
    this.subtitle = (data.channel || "") + " · " + (data.duration_str || "") + views;
    this.formats = data.formats || [];
    this.showFormats = false;

    this.playlistAvailable = kind === "video+list";
    this.isPlaylist = false;
  } catch (err) {
    this.meta = false;
    this.errorMsg = err.message;
  } finally {
    this.inspecting = false;
  }
};

// Pegar en el input dispara el análisis (cero taps extra en mobile).
window.og.onPaste = function(e) {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  this.url = text.trim();
  if (this.url) this.inspect();
};

// Botón "Pegar": lee el portapapeles y analiza de una.
window.og.pasteFromClipboard = async function() {
  try {
    const text = await navigator.clipboard.readText();
    if (text && text.trim()) {
      this.url = text.trim();
      this.inspect();
    }
  } catch (e) {
    this.errorMsg = this.t('ui.error_clipboard');
  }
};

// Soltar una URL (o texto con link) sobre la tarjeta también analiza.
window.og.onDrop = function(e) {
  const text = (e.dataTransfer?.getData("text") || "").trim();
  if (text) {
    this.url = text;
    this.inspect();
  }
};

// Limpiar el campo y resetear el estado analizado; devolver el foco.
window.og.clearUrl = function() {
  this.url = "";
  this.resetUI();
  this.$nextTick(() => document.getElementById("url")?.focus());
};

window.og.copyCommand = async function() {
  const fmt = this.FORMATS[this.quality] || this.quality;
  const cmd = 'yt-dlp -f "' + fmt + '" --merge-output-format mp4 "' + this.url.trim() + '"';
  try {
    await navigator.clipboard.writeText(cmd);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = cmd; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta);
  }
  this.cmdCopied = true;
  setTimeout(() => { this.cmdCopied = false; }, 2000);
};
