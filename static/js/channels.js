window.og = window.og || {};

// ── Channels ──────────────────────────────
window.og.channelsOpen = false;
window.og.channelsLoaded = false;
window.og.channels = [];
window.og.newChannelUrl = "";
window.og.newChannelQuality = "best";
window.og.newChannelInterval = 60;

window.og.toggleChannels = async function() {
  this.channelsOpen = !this.channelsOpen;
  if (this.channelsOpen && !this.channelsLoaded) {
    await this.loadChannels();
  }
};

window.og.channelsBadge = function() {
  if (this.channelsOpen || !this.channelsLoaded) return "";
  const n = (this.channels || []).length;
  return n > 0 ? " · " + n : "";
};

window.og.loadChannels = async function() {
  try {
    const r = await fetch("/api/channels");
    this.channels = await r.json();
  } catch (e) {
    this.channels = [];
  }
  this.channelsLoaded = true;
};

window.og.addChannel = async function() {
  if (!this.newChannelUrl.trim()) return;
  try {
    const r = await fetch("/api/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: this.newChannelUrl.trim(),
        quality: this.newChannelQuality,
        interval_minutes: parseInt(this.newChannelInterval) || 60,
      }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || this.t('ui.error'));
    this.newChannelUrl = "";
    await this.loadChannels();
  } catch (err) {
    this.errorMsg = err.message;
  }
};

window.og.toggleChannel = async function(ch) {
  try {
    const r = await fetch("/api/channels/" + ch.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: ch.enabled ? 0 : 1 }),
    });
    if (r.ok) ch.enabled = ch.enabled ? 0 : 1;
  } catch (e) {}
};

window.og.checkChannel = async function(channelId) {
  const ch = this.channels.find(c => c.id === channelId);
  if (!ch) return;
  ch._checking = true;
  try {
    const r = await fetch("/api/channels/" + channelId + "/check", { method: "POST" });
    const d = await r.json();
    if (d.new_videos > 0) this.errorMsg = this.t('ui.videos_found', {count: d.new_videos});
  } catch (err) {
    this.errorMsg = this.t('ui.error_check_channel');
  }
  ch._checking = false;
};

window.og.deleteChannel = async function(channelId) {
  try {
    await fetch("/api/channels/" + channelId, { method: "DELETE" });
    await this.loadChannels();
  } catch (e) {}
};
