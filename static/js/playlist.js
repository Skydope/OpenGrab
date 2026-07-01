window.og = window.og || {};

// ── Playlist ──────────────────────────────
window.og.isPlaylist = false;
window.og.playlistAvailable = false;
window.og.playlistCount = 0;
window.og.playlistTitle = "";
window.og.playlistVideos = [];
window.og.playlistLoading = false;
window.og.saveInSubfolder = false;

// ── Batch Playlist Download ────────────────
window.og.batchJobIds = [];
window.og.batchJobs = {};      // {jobId: {status, percent, speed, eta, error, filename, title}}
window.og.batchPollTimer = null;
window.og.batchDone = false;
window.og.batchStartTime = null;

window.og.loadPlaylist = async function() {
  if (!this.url || !this.url.trim()) {
    this.errorMsg = this.t('ui.error_url_first');
    return;
  }
  this.playlistLoading = true;
  try {
    const r = await fetch("/api/playlist?url=" + encodeURIComponent(this.url.trim()),
      { credentials: "include" });
    if (!r.ok) {
      let msg = this.t('ui.error_playlist_load');
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg + " (HTTP " + r.status + ")");
    }
    const data = await r.json();
    this.playlistVideos = (data.videos || []).map(v => ({
      ...v,
      selected: false,
    }));
    this.playlistCount = data.count || 0;
    this.playlistTitle = data.title || "";
    this.saveInSubfolder = false;
  } catch (err) {
    this.playlistVideos = [];
    this.errorMsg = err.message;
    console.error("loadPlaylist failed:", err);
  } finally {
    this.playlistLoading = false;
  }
};

window.og.selectedCount = function() {
  return this.playlistVideos.filter(v => v.selected).length;
};

window.og.saveExtraPref = function(type) {
  const key = type === "subs" ? "subs_default" : type === "thumb" ? "thumb_default" : "infojson_default";
  const val = type === "subs" ? this.subsEnabled : type === "thumb" ? this.thumbEnabled : this.infojsonEnabled;
  fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [key]: String(val) }),
  }).catch(() => {});
};

window.og.toggleAllPlaylist = function(val) {
  this.playlistVideos.forEach(v => {
    if (!v.unavailable) v.selected = val;
  });
};

window.og.downloadPlaylist = async function() {
  const selected = this.playlistVideos.filter(v => v.selected && v.url);
  if (!selected.length) { this.errorMsg = this.t('ui.select_at_least_one'); return; }
  this.resetUI();
  this.downloading = true;
  this.addLine("$", "playlist: " + selected.length + " videos");

  // Collect URLs for batch download
  const urls = selected.map(v => v.url);

  try {
    const r = await fetch("/api/playlist/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        urls: urls,
        quality: this.quality,
        playlist_title: this.playlistTitle,
        save_subfolder: this.saveInSubfolder,
      }),
    });

    if (r.status === 429) {
      this.errorMsg = this.t('ui.error_rate_limit_short');
      this.downloading = false;
      return;
    }
    if (r.status === 507) {
      this.errorMsg = this.t('ui.error_storage_full_short');
      this.downloading = false;
      return;
    }
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      this.errorMsg = data.detail || this.t('ui.error_batch_failed');
      this.downloading = false;
      return;
    }

    const data = await r.json();
    this.batchJobIds = data.job_ids || [];
    this.batchStartTime = Date.now();
    this.batchDone = false;
    this.batchJobs = {};

    // Start polling
    this.batchPollTimer = setInterval(() => this.pollBatchStatus(), 2000);
    // Initial poll immediately
    this.pollBatchStatus();

  } catch (err) {
    this.errorMsg = this.t('ui.error_network_short');
    this.downloading = false;
    console.error("downloadPlaylist failed:", err);
  }
};

window.og.pollBatchStatus = function() {
  if (this.batchDone || !this.batchJobIds.length) return;

  // Safety timeout check
  if (this.batchStartTime && Date.now() - this.batchStartTime > 60 * 60 * 1000) {
    this.batchDone = true;
    if (this.batchPollTimer) {
      clearInterval(this.batchPollTimer);
      this.batchPollTimer = null;
    }
    // Show warning but still show final status
  }

  const ids = this.batchJobIds.join(",");
  fetch(`/api/jobs/batch-status?ids=${ids}`, {credentials: 'include'})
    .then(r => r.json())
    .then(jobs => {
      jobs.forEach(j => {
        this.batchJobs[j.job_id] = j;
      });

      // Check if all terminal
      const allTerminal = this.batchJobIds.every(id => {
        const job = this.batchJobs[id];
        return job && ['done', 'error', 'interrupted'].includes(job.status);
      });

      if (allTerminal) {
        this.batchDone = true;
        if (this.batchPollTimer) {
          clearInterval(this.batchPollTimer);
          this.batchPollTimer = null;
        }
        // Summary
        const ok = this.batchJobIds.filter(id => this.batchJobs[id]?.status === 'done').length;
        const fail = this.batchJobIds.filter(id => this.batchJobs[id]?.status === 'error').length;
        const skipped = this.batchJobIds.filter(id => this.batchJobs[id]?.status === 'interrupted').length;
        this.addLine(
          fail === 0 ? "✓" : "✗",
          this.t('ui.batch_summary', {ok: ok, failed: fail, skipped: skipped}),
          fail === 0 ? "ok" : "err"
        );
        this.downloading = false;
        if (this.historyLoaded) this.loadHistory();
        // Success banner: mismo patrón que descarga individual.
        // Todos los jobs del batch comparten el mismo workdir:
        // abrir la carpeta de cualquiera muestra todos los archivos.
        if (ok > 0) {
          const firstDone = this.batchJobIds.find(id => this.batchJobs[id]?.status === 'done');
          if (firstDone) {
            this.successPath = this.t('ui.playlist_success', {ok: ok, failed: fail, skipped: skipped});
            this.lastJobId = firstDone;
          }
        }
      }
    })
    .catch(err => {});
};
