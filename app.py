#!/usr/bin/env python3
"""
ytgrab — descargador self-hosted de YouTube a MP4 (wrapper de yt-dlp).

Un solo archivo. Levanta un server local, sirve la UI y descarga del lado
del servidor usando yt-dlp + ffmpeg. Pensado para correr en una LAN/homelab.

Uso:
    pip install -r requirements.txt   # fastapi, uvicorn, yt-dlp
    # ffmpeg tiene que estar en el PATH (apt install ffmpeg / pacman -S ffmpeg)
    python app.py
    # -> http://127.0.0.1:8800

Variables de entorno:
    YTGRAB_HOST   (default 127.0.0.1)
    YTGRAB_PORT   (default 8800)
    YTGRAB_DIR    (default ./downloads)  carpeta de salida
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import yt_dlp


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOST = os.environ.get("YTGRAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("YTGRAB_PORT", "8800"))
OUT_DIR = Path(os.environ.get("YTGRAB_DIR", "./downloads")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_old_workdirs() -> None:
    """Borra workdirs temporales de yt-dlp que hayan quedado de ejecuciones
    anteriores (más de 24 h)."""
    cutoff = time.time() - 86400
    count = 0
    for d in OUT_DIR.glob("ytgrab_*"):
        if d.is_dir():
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d)
                    count += 1
            except OSError:
                pass
    if count:
        log.info("limpiados %d workdirs viejos", count)


_cleanup_old_workdirs()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ytgrab] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ytgrab")

TOKEN = os.environ.get("YTGRAB_TOKEN", "").strip()
MAX_JOBS = int(os.environ.get("YTGRAB_MAX_JOBS", "2"))
MAX_SIZE_MB = max(0, int(os.environ.get("YTGRAB_MAX_SIZE_MB", "0")))
HISTORY_FILE = OUT_DIR / ".ytgrab_history.json"
HISTORY_MAX = 500


def _load_history() -> list:
    try:
        return _json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return []


def _save_history(entries: list) -> None:
    entries = entries[-HISTORY_MAX:]
    try:
        HISTORY_FILE.write_text(_json.dumps(entries, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass


HISTORY: list = _load_history()


def require_auth(request: Request) -> None:
    """Dependencia de FastAPI que exige token si YTGRAB_TOKEN está seteado."""
    if not TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:] == TOKEN:
        return
    if request.query_params.get("token") == TOKEN:
        return
    raise HTTPException(401, "Token requerido. Usá Authorization: Bearer <token> o ?token=…")


app = FastAPI(title="ytgrab")

# Estado de jobs en memoria. Tool de un solo usuario / homelab: alcanza y sobra.
JOBS: Dict[str, Dict[str, Any]] = {}

# Selectores de formato para yt-dlp. Todos terminan remuxeando a mp4
# (merge_output_format). Se prefiere h264/m4a para maxima compatibilidad.
FORMATS: Dict[str, str] = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bv*+ba/b",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
    "audio": "bestaudio/best",  # se posprocesa a mp3
}


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
class JobReq(BaseModel):
    url: str
    quality: str = "best"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_YT_RE = re.compile(
    r"^https?://(www\.|m\.|music\.)?"
    r"(youtube\.com/(watch\?|shorts/|live/|playlist\?|embed/)|youtu\.be/)",
    re.IGNORECASE,
)


def _looks_like_youtube(url: str) -> bool:
    return bool(_YT_RE.match(url.strip()))


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\s.\-()\[\]]", "", name, flags=re.UNICODE).strip()
    return (name or "video")[:120]


def _fetch_info(url: str) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("yt-dlp no devolvió información.")
    return info


def _fetch_playlist(url: str) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("yt-dlp no devolvió información.")
    entries = info.get("entries") or []
    videos = []
    for e in entries:
        if e is None:
            continue
        vid_url = e.get("url") or e.get("webpage_url") or ""
        if vid_url:
            videos.append({
                "title": e.get("title", "—"),
                "url": vid_url,
                "duration": e.get("duration"),
            })
    return {"title": info.get("title", "—"), "count": len(videos), "videos": videos}


def _run_download(job_id: str, url: str, quality: str) -> None:
    job = JOBS[job_id]
    workdir = Path(tempfile.mkdtemp(prefix="ytgrab_", dir=OUT_DIR))
    job["workdir"] = str(workdir)

    def hook(d: Dict[str, Any]) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0.0
            job.update(
                status="downloading",
                percent=round(pct, 1),
                speed=d.get("_speed_str", "").strip(),
                eta=d.get("_eta_str", "").strip(),
                downloaded=done,
                total=total,
            )
            job["event"].set()
        elif status == "finished":
            job.update(status="processing", percent=100.0,
                       note="Muxeando / remuxeando a mp4…")
            job["event"].set()

    is_audio = quality == "audio"
    outtmpl = str(workdir / "%(title)s.%(ext)s")
    fmt = FORMATS.get(quality, FORMATS["best"])
    if MAX_SIZE_MB and not is_audio:
        fmt += f"[filesize_approx<{MAX_SIZE_MB}M]"
    opts: Dict[str, Any] = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "postprocessors": [],
    }
    if is_audio:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": "192"}
        ]
        opts.pop("merge_output_format", None)

    try:
        job.update(status="starting", percent=0.0)
        log.info("job %s: iniciando descarga (%s, %s)", job_id, quality, url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Resolver el archivo final ya posprocesado.
        produced = sorted(
            workdir.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        produced = [p for p in produced if p.is_file()]
        if not produced:
            raise RuntimeError("No se generó ningún archivo.")
        final = produced[0]

        title = _safe_name(info.get("title", "video"))
        ext = final.suffix.lstrip(".")
        job.update(
            status="done",
            percent=100.0,
            filepath=str(final),
            filename=f"{title}.{ext}",
            mime="audio/mpeg" if is_audio else "video/mp4",
        )
        log.info("job %s: completado → %s", job_id, f"{title}.{ext}")
        HISTORY.append({
            "url": url, "title": title, "quality": quality,
            "filename": f"{title}.{ext}", "size": final.stat().st_size,
            "job_id": job_id, "completed": int(time.time()),
        })
        _save_history(HISTORY)
        job["event"].set()
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
        job["event"].set()
        log.error("job %s: falló — %s", job_id, exc)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/info")
async def api_info(url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_youtube(url):
        raise HTTPException(400, "Eso no parece una URL de YouTube.")
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer el video: {exc}")

    dur = info.get("duration") or 0
    return JSONResponse({
        "title": info.get("title", "—"),
        "channel": info.get("uploader") or info.get("channel") or "—",
        "duration": dur,
        "duration_str": time.strftime("%H:%M:%S", time.gmtime(dur)) if dur else "—",
        "thumbnail": info.get("thumbnail"),
        "view_count": info.get("view_count"),
    })


@app.get("/api/playlist")
async def api_playlist(url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_youtube(url):
        raise HTTPException(400, "Eso no parece una URL de YouTube.")
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer la playlist: {exc}")
    return JSONResponse(info)


@app.post("/api/jobs")
async def api_create_job(req: JobReq, _: None = Depends(require_auth)):
    url = req.url.strip()
    if not _looks_like_youtube(url):
        raise HTTPException(400, "Eso no parece una URL de YouTube.")
    if req.quality not in FORMATS:
        raise HTTPException(400, "Calidad inválida.")
    active = sum(1 for j in JOBS.values() if j.get("status") in
                 ("queued", "starting", "downloading", "processing"))
    if active >= MAX_JOBS:
        raise HTTPException(429, f"Límite de {MAX_JOBS} descarga(s) simultánea(s). Esperá que termine una.")

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "status": "queued", "percent": 0.0,
                    "created": time.time(), "event": asyncio.Event()}
    log.info("job %s: creado (%s, %s)", job_id, req.quality, req.url)
    # Lanzar en thread aparte (yt-dlp es bloqueante).
    asyncio.create_task(asyncio.to_thread(_run_download, job_id, url, req.quality))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/events")
async def api_job_events(job_id: str, _: None = Depends(require_auth)):
    if job_id not in JOBS:
        raise HTTPException(404, "Job no encontrado.")

    async def stream():
        last = None
        while True:
            job = JOBS.get(job_id)
            if job is None:
                break
            event = job.get("event")
            if event:
                try:
                    await asyncio.wait_for(event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                event.clear()
            snapshot = {k: job.get(k) for k in
                        ("status", "percent", "speed", "eta", "note",
                         "filename", "error")}
            if snapshot != last:
                yield f"data: {_json.dumps(snapshot)}\n\n"
                last = snapshot
            if job.get("status") in ("done", "error"):
                break

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job_id}/file")
async def api_job_file(job_id: str, _: None = Depends(require_auth)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado.")
    if job.get("status") != "done":
        raise HTTPException(409, "El archivo todavía no está listo.")
    path = job.get("filepath")
    if not path or not Path(path).exists():
        raise HTTPException(410, "El archivo ya no está disponible.")
    log.info("job %s: sirviendo archivo → %s", job_id, job.get("filename", ""))
    return FileResponse(path, media_type=job.get("mime", "application/octet-stream"),
                        filename=job.get("filename", "download"))


@app.get("/api/history")
async def api_history(limit: int = 20, _: None = Depends(require_auth)):
    entries = HISTORY[-max(1, min(limit, HISTORY_MAX)):]
    return JSONResponse(list(reversed(entries)))


@app.get("/health")
async def health():
    active = sum(1 for j in JOBS.values() if j["status"] in
                 ("queued", "starting", "downloading", "processing"))
    return {"status": "ok", "jobs_active": active}


@app.get("/", response_class=HTMLResponse)
async def index():
    html = INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    return HTMLResponse(html)


# --------------------------------------------------------------------------- #
# UI (una sola página, sin dependencias externas)
# --------------------------------------------------------------------------- #
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ytgrab</title>
<style>
  :root{
    --ink:#0E1116; --panel:#171B22; --panel-2:#1C212B; --line:#262C36;
    --text:#D7DCE3; --muted:#8A93A0; --faint:#5C6670;
    --amber:#E8A02C; --ok:#5BC97E; --danger:#E5484D;
    --mono:ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:var(--ink); color:var(--text); font-family:var(--sans);
    min-height:100vh; line-height:1.5;
    -webkit-font-smoothing:antialiased;
    display:flex; justify-content:center; padding:48px 20px 80px;
  }
  .wrap{width:100%; max-width:680px}

  header{display:flex; align-items:baseline; gap:12px; margin-bottom:28px}
  .logo{
    font-family:var(--mono); font-size:22px; font-weight:600; letter-spacing:-.5px;
    color:var(--text);
  }
  .logo b{color:var(--amber)}
  .tag{font-family:var(--mono); font-size:12px; color:var(--muted)}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);
       display:inline-block;margin-right:6px;vertical-align:middle}

  .card{
    background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:20px;
  }
  label.fld{display:block; font-size:12px; color:var(--muted); font-family:var(--mono);
            text-transform:uppercase; letter-spacing:.08em; margin-bottom:8px}

  .row{display:flex; gap:10px}
  input[type=text]{
    flex:1; background:var(--ink); border:1px solid var(--line); color:var(--text);
    font-family:var(--mono); font-size:14px; padding:12px 14px; border-radius:8px;
    outline:none; min-width:0;
  }
  input[type=text]:focus{border-color:var(--amber)}
  input[type=text]::placeholder{color:var(--faint)}

  button{
    font-family:var(--sans); font-weight:600; font-size:14px; cursor:pointer;
    border:1px solid var(--line); background:var(--panel-2); color:var(--text);
    padding:12px 18px; border-radius:8px; transition:.12s;
  }
  button:hover:not(:disabled){border-color:var(--muted)}
  button:disabled{opacity:.45; cursor:not-allowed}
  button.go{background:var(--amber); color:#1a1206; border-color:var(--amber)}
  button.go:hover:not(:disabled){filter:brightness(1.08)}

  .meta{
    display:flex; gap:14px; margin-top:18px; padding-top:18px;
    border-top:1px solid var(--line);
  }
  .meta.hide{display:none}
  .thumb{width:120px; aspect-ratio:16/9; border-radius:6px; object-fit:cover;
         background:var(--ink); border:1px solid var(--line); flex:none}
  .meta .info{min-width:0}
  .meta .t{font-weight:600; font-size:15px; margin:0 0 4px;
           overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .meta .s{font-size:13px; color:var(--muted); font-family:var(--mono)}

  .quality{display:flex; gap:8px; flex-wrap:wrap; margin-top:18px}
  .chip{
    font-family:var(--mono); font-size:13px; padding:7px 13px; border-radius:999px;
    border:1px solid var(--line); background:var(--ink); color:var(--muted);
    cursor:pointer; user-select:none; transition:.12s;
  }
  .chip[aria-pressed=true]{border-color:var(--amber); color:var(--amber);
                           background:rgba(232,160,44,.08)}

  /* signature: log estilo terminal con la salida del job */
  .term{
    margin-top:18px; background:#0A0D12; border:1px solid var(--line);
    border-radius:8px; font-family:var(--mono); font-size:13px;
    color:var(--muted); overflow:hidden; display:none;
  }
  .term.show{display:block}
  .term .bar-head{
    display:flex; align-items:center; gap:8px; padding:9px 12px;
    border-bottom:1px solid var(--line); color:var(--faint); font-size:11px;
    letter-spacing:.05em;
  }
  .term .bar-head .tl{display:flex; gap:6px}
  .term .bar-head .tl i{width:10px;height:10px;border-radius:50%;display:block}
  .term .lines{padding:12px; max-height:200px; overflow:auto; white-space:pre-wrap}
  .term .lines .l{margin:0}
  .term .lines .l .pfx{color:var(--amber)}
  .term .lines .l.ok .pfx{color:var(--ok)}
  .term .lines .l.err{color:var(--danger)}
  .term .lines .l.err .pfx{color:var(--danger)}

  .progress{height:4px; background:var(--ink); position:relative}
  .progress > i{display:block; height:100%; width:0%; background:var(--amber);
                transition:width .25s}
  .progress.done > i{background:var(--ok)}

  .actions{display:none; margin-top:18px}
  .actions.show{display:block}
  a.dl{
    display:inline-flex; align-items:center; gap:8px; text-decoration:none;
    background:var(--ok); color:#06210f; font-weight:600; font-size:14px;
    padding:12px 18px; border-radius:8px; font-family:var(--sans);
  }
  a.dl:hover{filter:brightness(1.06)}

  .err-box{
    margin-top:14px; display:none; padding:12px 14px; border-radius:8px;
    border:1px solid rgba(229,72,77,.4); background:rgba(229,72,77,.07);
    color:#f3a3a5; font-size:13px; font-family:var(--mono);
  }
  .err-box.show{display:block}

  .auth-card{display:none}
  .auth-card.show{display:block}

  .pl-video{display:flex; align-items:center; gap:10px; padding:8px 12px;
            border-bottom:1px solid var(--line); font-size:13px}
  .pl-video:last-child{border-bottom:0}
  .pl-video:hover{background:rgba(255,255,255,.02)}
  .pl-video .pl-title{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
                        white-space:nowrap; color:var(--text)}
  .pl-video .pl-dur{color:var(--muted); font-family:var(--mono); font-size:11px; flex:none}
  .pl-video input[type=checkbox]{flex:none; accent-color:var(--amber)}

  .h-entry{font-family:var(--mono); font-size:12px; padding:5px 0; border-bottom:1px solid var(--line);
           display:flex; gap:10px; align-items:baseline}
  .h-entry .h-title{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text)}
  .h-entry .h-meta{color:var(--muted); font-size:11px; flex:none}
  .h-entry .h-size{color:var(--faint); font-size:11px; flex:none}

  footer{margin-top:26px; font-size:12px; color:var(--faint); font-family:var(--mono);
         line-height:1.7}
  footer code{color:var(--muted)}

  @media (max-width:520px){
    body{padding:28px 14px 60px}
    .row{flex-direction:column}
    .meta{flex-direction:column}
    .thumb{width:100%}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="logo">yt<b>grab</b></span>
    <span class="tag"><span class="dot"></span>self-hosted · yt-dlp</span>
  </header>

  <div class="card auth-card" id="authcard">
    <label class="fld" for="token">Token de acceso</label>
    <div class="row">
      <input id="token" type="password" placeholder="Ingresá el token configurado en YTGRAB_TOKEN"
             autocomplete="off" spellcheck="false">
      <button id="auth-btn" class="go">Conectar</button>
    </div>
  </div>

  <div class="card">
    <label class="fld" for="url">URL de YouTube</label>
    <div class="row">
      <input id="url" type="text" placeholder="https://www.youtube.com/watch?v=…"
             autocomplete="off" spellcheck="false">
      <button id="inspect" class="go">Analizar</button>
    </div>

    <div class="meta" id="meta">
      <img class="thumb" id="thumb" alt="">
      <div class="info">
        <p class="t" id="m-title">—</p>
        <p class="s" id="m-sub">—</p>
      </div>
    </div>

    <div class="quality" id="quality">
      <span class="chip" data-q="best"  aria-pressed="true">best mp4</span>
      <span class="chip" data-q="1080p" aria-pressed="false">1080p</span>
      <span class="chip" data-q="720p"  aria-pressed="false">720p</span>
      <span class="chip" data-q="480p"  aria-pressed="false">480p</span>
      <span class="chip" data-q="audio" aria-pressed="false">solo audio · mp3</span>
    </div>

    <div id="playlist-panel" style="display:none; margin-top:14px">
      <div style="display:flex; gap:8px; align-items:center; margin-bottom:10px">
        <span id="pl-count" style="font-family:var(--mono); font-size:13px; color:var(--muted)"></span>
        <button id="pl-dl-all" style="font-size:12px; padding:6px 12px">Descargar playlist</button>
      </div>
      <div id="pl-videos" style="max-height:300px; overflow:auto; border:1px solid var(--line); border-radius:8px"></div>
    </div>

    <div style="margin-top:18px">
      <button id="grab" disabled style="width:100%">Descargar</button>
    </div>

    <div class="term" id="term">
      <div class="bar-head">
        <span class="tl"><i style="background:#E5484D"></i><i style="background:#E8A02C"></i><i style="background:#5BC97E"></i></span>
        <span>yt-dlp</span>
      </div>
      <div class="progress" id="prog"><i id="prog-bar"></i></div>
      <div class="lines" id="lines"></div>
    </div>

    <div class="err-box" id="errbox"></div>

    <div class="actions" id="actions">
      <a class="dl" id="dl" href="#" download>↓ Guardar archivo</a>
    </div>
  </div>

  <footer>
    Corre en tu red. El servidor descarga con <code>yt-dlp</code> + <code>ffmpeg</code> y te entrega el <code>.mp4</code>.<br>
    Usalo solo con contenido propio, con licencia que lo permita, o bajo fair use.
  </footer>

  <div id="history-panel" style="margin-top:14px; display:none">
    <div style="font-family:var(--mono); font-size:11px; color:var(--faint); text-transform:uppercase; letter-spacing:.08em; margin-bottom:8px; cursor:pointer" id="history-toggle">historial ▸</div>
    <div id="history-list" style="display:none"></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const AUTH_REQUIRED = "__AUTH_REQUIRED__" === "true";
let quality = "best";
let currentMeta = null;
let _authToken = "";

function apiFetch(url, opts={}){
  opts = Object.assign({}, opts);
  if(_authToken){
    opts.headers = Object.assign({}, opts.headers||{});
    opts.headers["Authorization"] = "Bearer " + _authToken;
  }
  return fetch(url, opts);
}

function saveToken(tok){ _authToken = tok; sessionStorage.setItem("ytgrab-token", tok); }
function loadToken(){ return sessionStorage.getItem("ytgrab-token") || ""; }

async function initAuth(){
  if(!AUTH_REQUIRED) return;
  const saved = loadToken();
  if(saved){
    _authToken = saved;
    try{
      const r = await apiFetch("/api/info?url=http://x");
      if(r.status === 401) throw new Error("token-invalid");
    }catch(e){
      if(e.message === "token-invalid") _authToken = "";
    }
  }
  if(!_authToken){
    $("#authcard").classList.add("show");
    document.querySelector(".card:not(.auth-card)").style.display = "none";
  }
}
initAuth();

$("#auth-btn").addEventListener("click", async () => {
  const tok = $("#token").value.trim();
  if(!tok) return;
  $("#auth-btn").disabled = true;
  $("#auth-btn").textContent = "…";
  _authToken = tok;
  const r = await apiFetch("/api/info?url=http://x");
  if(r.status === 401){
    _authToken = "";
    showError("Token inválido.");
    $("#auth-btn").disabled = false;
    $("#auth-btn").textContent = "Conectar";
    return;
  }
  if(r.ok || r.status === 400){  // 400 = URL inválida, pero token aceptado
    saveToken(tok);
    $("#authcard").classList.remove("show");
    document.querySelector(".card:not(.auth-card)").style.display = "";
  }
  $("#auth-btn").disabled = false;
  $("#auth-btn").textContent = "Conectar";
});

$("#token").addEventListener("keydown", e => {
  if(e.key === "Enter") $("#auth-btn").click();
});

function termLine(text, cls=""){
  const lines = $("#lines");
  const p = document.createElement("p");
  p.className = "l " + cls;
  p.innerHTML = '<span class="pfx">$ </span>' + text;
  lines.appendChild(p);
  lines.scrollTop = lines.scrollHeight;
}
function resetUI(){
  $("#term").classList.remove("show");
  $("#prog").classList.remove("done");
  $("#prog-bar").style.width = "0%";
  $("#lines").innerHTML = "";
  $("#actions").classList.remove("show");
  $("#errbox").classList.remove("show");
  $("#errbox").textContent = "";
  $("#playlist-panel").style.display = "none";
}
function showError(msg){
  $("#errbox").textContent = msg;
  $("#errbox").classList.add("show");
}

// chips de calidad
$("#quality").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if(!chip) return;
  document.querySelectorAll(".chip").forEach(c => c.setAttribute("aria-pressed","false"));
  chip.setAttribute("aria-pressed","true");
  quality = chip.dataset.q;
});

// analizar
$("#inspect").addEventListener("click", inspect);
$("#url").addEventListener("keydown", e => { if(e.key==="Enter") inspect(); });

async function inspect(){
  const url = $("#url").value.trim();
  resetUI();
  $("#meta").classList.remove("hide");
  $("#grab").disabled = true;
  if(!url){ showError("Pegá una URL primero."); return; }
  $("#inspect").disabled = true;
  $("#inspect").textContent = "…";
  try{
    const r = await apiFetch("/api/info?url=" + encodeURIComponent(url));
    if(!r.ok){
      let msg = "Error al analizar.";
      try{ msg = (await r.json()).detail || msg; }catch(e){}
      throw new Error(msg);
    }
    const data = await r.json();
    currentMeta = data;
    $("#thumb").src = data.thumbnail || "";
    $("#m-title").textContent = data.title;
    const views = data.view_count ? " · " + Intl.NumberFormat("es-AR").format(data.view_count) + " views" : "";
    $("#m-sub").textContent = data.channel + " · " + data.duration_str + views;
    $("#meta").classList.add("show");
    $("#grab").disabled = false;

    if(looksLikePlaylist(url)){
      $("#playlist-panel").style.display = "block";
      loadPlaylist(url);
    }else{
      $("#playlist-panel").style.display = "none";
    }
  }catch(err){
    $("#meta").classList.remove("show");
    showError(err.message);
  }finally{
    $("#inspect").disabled = false;
    $("#inspect").textContent = "Analizar";
  }
}

// descargar
function looksLikePlaylist(url){
  return /[?&]list=/.test(url);
}

async function loadPlaylist(url){
  const panel = $("#pl-videos");
  const count = $("#pl-count");
  panel.innerHTML = '<span style="font-size:12px;color:var(--muted);padding:12px;display:block">Cargando playlist…</span>';
  try{
    const r = await apiFetch("/api/playlist?url=" + encodeURIComponent(url));
    if(!r.ok){
      let msg = "Error al cargar playlist.";
      try{ msg = (await r.json()).detail || msg; }catch(e){}
      throw new Error(msg);
    }
    const data = await r.json();
    window._playlistVideos = data;
    count.textContent = data.count + " videos";
    panel.innerHTML = "";
    data.videos.forEach((v, i) => {
      const dur = v.duration ? new Date(v.duration*1000).toISOString().substr(11,8).replace(/^00:/,"") : "";
      const div = document.createElement("div");
      div.className = "pl-video";
      div.innerHTML = '<input type=checkbox checked data-idx='+i+'>'+
        '<span class="pl-title" title="'+v.title+'">'+v.title+'</span>'+
        (dur ? '<span class="pl-dur">'+dur+'</span>' : '');
      panel.appendChild(div);
    });
  }catch(err){
    panel.innerHTML = '<span style="font-size:12px;color:var(--danger);padding:12px;display:block">'+err.message+'</span>';
  }
}

async function downloadSelected(){
  const checks = document.querySelectorAll("#pl-videos input[type=checkbox]:checked");
  if(!checks.length){ showError("Seleccioná al menos un video."); return; }
  const data = window._playlistVideos;
  if(!data) return;
  resetUI();
  $("#term").classList.add("show");
  termLine("playlist: " + data.title + " (" + checks.length + " videos)");
  const url = $("#url").value.trim();
  $("#grab").disabled = true;
  let ok = 0, fail = 0;

  for(const cb of checks){
    const v = data.videos[cb.dataset.idx];
    if(!v || !v.url) { fail++; continue; }
    termLine("iniciando: " + v.title);
    let jobId;
    try{
      const r = await apiFetch("/api/jobs", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({url: v.url, quality})
});

// historial
let _historyLoaded = false;
$("#history-toggle").addEventListener("click", async () => {
  const list = $("#history-list");
  if(list.style.display === "none"){
    if(!_historyLoaded){
      list.innerHTML = '<span style="font-size:12px;color:var(--muted)">Cargando…</span>';
      try{
        const r = await apiFetch("/api/history?limit=15");
        const entries = await r.json();
        list.innerHTML = "";
        if(!entries.length){
          list.innerHTML = '<span style="font-size:12px;color:var(--muted)">Sin descargas todavía.</span>';
        }else{
          entries.forEach(e => {
            const el = document.createElement("div");
            el.className = "h-entry";
            const sizeMb = e.size ? (e.size/1048576).toFixed(1) + " MB" : "";
            el.innerHTML = '<span class="h-title" title="'+e.title+'">'+e.title+
              '</span><span class="h-meta">'+e.quality+'</span>'+
              (sizeMb ? '<span class="h-size">'+sizeMb+'</span>' : '');
            list.appendChild(el);
          });
        }
      }catch(err){
        list.innerHTML = '<span style="font-size:12px;color:var(--danger)">Error al cargar.</span>';
      }
      _historyLoaded = true;
    }
    list.style.display = "block";
    $("#history-toggle").textContent = "historial ▾";
  }else{
    list.style.display = "none";
    $("#history-toggle").textContent = "historial ▸";
  }
});

// mostrar panel de historial siempre
$("#history-panel").style.display = "block";
      if(!r.ok){
        let msg = "No se pudo crear el job.";
        try{ msg = (await r.json()).detail || msg; }catch(e){}
        throw new Error(msg);
      }
      const jd = await r.json();
      jobId = jd.job_id;
    }catch(err){
      termLine("error: " + err.message, "err");
      fail++; continue;
    }

    // wait for this job to complete via SSE
    await new Promise((resolve) => {
      const ev = new EventSource("/api/jobs/" + jobId + "/events" + (_authToken ? "?token=" + encodeURIComponent(_authToken) : ""));
      ev.onmessage = m => {
        const s = JSON.parse(m.data);
        if(s.status === "downloading" && s.speed){
          const lines = $("#lines");
          const last = lines.lastElementChild;
          const txt = "  " + (s.percent||0).toFixed(1) + "%  " + (s.speed||"") + (s.eta ? "  ETA " + s.eta : "");
          if(last && last.dataset.live){
            last.querySelector("span.body").textContent = txt;
          }else{
            const p = document.createElement("p");
            p.className = "l"; p.dataset.live = "1";
            p.innerHTML = '<span class="pfx">› </span><span class="body"></span>';
            p.querySelector("span.body").textContent = txt;
            lines.appendChild(p);
          }
          lines.scrollTop = lines.scrollHeight;
        }
        if(s.status === "done"){
          termLine("ok: " + v.title, "ok");
          ok++; ev.close(); resolve();
        }
        if(s.status === "error"){
          termLine("error: " + (s.error||"desconocido"), "err");
          fail++; ev.close(); resolve();
        }
      };
      ev.onerror = () => { fail++; ev.close(); resolve(); };
    });
  }
  termLine("completado: " + ok + " ok, " + fail + " fallidos", ok>0 ? "ok" : "err");
  $("#grab").disabled = false;
}

$("#pl-dl-all").addEventListener("click", downloadSelected);

$("#grab").addEventListener("click", async () => {
  const url = $("#url").value.trim();
  if(!url) return;
  resetUI();
  $("#term").classList.add("show");
  $("#grab").disabled = true;
  termLine("yt-dlp -f " + quality + " " + url);

  let jobId;
  try{
    const r = await apiFetch("/api/jobs", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({url, quality})
    });
    if(!r.ok){
      let msg = "No se pudo crear el job.";
      try{ msg = (await r.json()).detail || msg; }catch(e){}
      throw new Error(msg);
    }
    const data = await r.json();
    jobId = data.job_id;
  }catch(err){
    showError(err.message);
    $("#grab").disabled = false;
    return;
  }

  const ev = new EventSource("/api/jobs/" + jobId + "/events" + (_authToken ? "?token=" + encodeURIComponent(_authToken) : ""));
  let lastStatus = "";
  ev.onmessage = m => {
    const s = JSON.parse(m.data);
    if(s.percent != null) $("#prog-bar").style.width = s.percent + "%";

    if(s.status !== lastStatus){
      lastStatus = s.status;
      if(s.status === "downloading") termLine("descargando streams…");
      if(s.status === "processing") termLine(s.note || "procesando…");
    }
    if(s.status === "downloading" && s.speed){
      // sobreescribe la última línea de progreso
      const lines = $("#lines");
      const last = lines.lastElementChild;
      const txt = "downloading  " + (s.percent||0).toFixed(1) + "%   " +
                  (s.speed||"") + (s.eta ? "   ETA " + s.eta : "");
      if(last && last.dataset.live){
        last.querySelector("span.body").textContent = txt;
      }else{
        const p = document.createElement("p");
        p.className = "l"; p.dataset.live = "1";
        p.innerHTML = '<span class="pfx">› </span><span class="body"></span>';
        p.querySelector("span.body").textContent = txt;
        lines.appendChild(p);
      }
      lines.scrollTop = lines.scrollHeight;
    }

    if(s.status === "done"){
      $("#prog-bar").style.width = "100%";
      $("#prog").classList.add("done");
      termLine("listo → " + s.filename, "ok");
      $("#dl").href = "/api/jobs/" + jobId + "/file";
      $("#dl").setAttribute("download", s.filename || "");
      $("#actions").classList.add("show");
      $("#grab").disabled = false;
      ev.close();
    }
    if(s.status === "error"){
      termLine("error: " + (s.error||"desconocido"), "err");
      showError(s.error || "Falló la descarga.");
      $("#grab").disabled = false;
      ev.close();
    }
  };
  ev.onerror = () => {
    termLine("conexión de eventos cerrada", "err");
    $("#grab").disabled = false;
    ev.close();
  };
});
</script>
</body>
</html>
"""


def main() -> None:
    log.info("ytgrab → http://%s:%d (salida: %s)", HOST, PORT, OUT_DIR)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
