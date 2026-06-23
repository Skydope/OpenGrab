from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import yt_dlp  # type: ignore[import-untyped]

from config import FORMATS, resource_path
from state import AppState

log = logging.getLogger("opengrab")

# --------------------------------------------------------------------------- #
# URL validation: universal pero SSRF-safe
# --------------------------------------------------------------------------- #
# OpenGrab es un frontend de yt-dlp (~1800 sitios), así que NO restringimos por
# plataforma: aceptamos cualquier http(s) público y dejamos que yt-dlp decida si
# puede extraerlo. Pero como yt-dlp hace requests del lado del servidor, mantenemos
# una defensa en profundidad contra SSRF: rechazamos esquemas no-http, localhost, y
# hosts que sean IP interna (privada/loopback/link-local/reservada) o el endpoint de
# metadata cloud (169.254.169.254, que cae en link-local).

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_safe_url(url: str) -> bool:
    """True si la URL es un http(s) publico seguro de pasar a yt-dlp.

    Universal en cuanto a sitio; restrictivo en cuanto a destino (anti-SSRF).

    NOTA: No cubre notaciones no-estandar de IP (decimal, hex, IPv4-mapped
    IPv6). Estos bypasses son conocidos pero de bajo riesgo en entornos
    self-hosted. Ver tests/test_helpers.py para la cobertura exacta."""
    try:
        parsed = urlparse(url.strip())
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    host_l = host.lower()
    if host_l in _BLOCKED_HOSTS or host_l.endswith((".local", ".localhost")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # hostname de dominio normal → ok (yt-dlp resuelve y baja)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _enforce_size(path: Path, max_mb: int) -> None:
    """Borra y falla si el archivo final supera el limite por-archivo.
    Cubre el caso donde filesize_approx subestimo o el filtro no aplico (audio)."""
    if max_mb and path.stat().st_size > max_mb * 1024 * 1024:
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"El archivo supera el limite de {max_mb} MB (OPENGRAB_MAX_SIZE_MB)."
        )


def _safe_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^\w \t.\-()\[\]]", "", name, flags=re.UNICODE).strip()
    return (name or "video")[:120]


def _sanitize_url(url: str) -> str:
    clean = re.sub(r"([?&]token=)[^&]+", r"\1***", url)
    clean = clean.replace("\n", "").replace("\r", "")
    return clean[:200]


# (patrón en minúsculas → mensaje humano). El primero que matchee gana.
_ERROR_MAP: list[tuple[str, str]] = [
    ("403", "YouTube rechazó la solicitud. Probá 'Actualizar motor' o esperá unos minutos."),
    ("404", "El video no existe o fue eliminado."),
    ("private video", "El video es privado."),
    ("members-only", "El video es solo para miembros del canal."),
    ("sign in to confirm your age", "El video requiere verificación de edad."),
    ("age", "El video requiere verificación de edad o inicio de sesión."),
    ("sign in", "El video requiere iniciar sesión."),
    ("not available in your country", "El video está bloqueado en tu región."),
    ("geo", "El video está bloqueado en tu región."),
    ("video unavailable", "El video no está disponible."),
    ("is not available", "El video no está disponible."),
    ("unsupported url", "URL no soportada por el motor de descarga."),
    ("ffmpeg", "Falló el procesamiento (ffmpeg). Si es un build de escritorio, reinstalá."),
    ("ffprobe", "Falló el procesamiento (ffmpeg). Si es un build de escritorio, reinstalá."),
    ("timed out", "Problema de red: la conexión expiró. Reintentá."),
    ("connection", "Problema de red. Revisá tu conexión y reintentá."),
    ("urlopen error", "Problema de red. Revisá tu conexión y reintentá."),
]


def _friendly_error(exc: Exception) -> str:
    """Traduce errores técnicos de yt-dlp a mensajes que un humano entiende.

    Los mensajes internos ya en español (límite de tamaño, 'No se generó…') se devuelven
    tal cual; lo desconocido cae al texto crudo recortado."""
    raw = str(exc)
    low = raw.lower()
    for needle, msg in _ERROR_MAP:
        if needle in low:
            return msg
    return raw[:300]


# --------------------------------------------------------------------------- #
# yt-dlp wrappers
# --------------------------------------------------------------------------- #
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
    return info  # type: ignore[no-any-return]


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
        videos.append({
            "title": e.get("title") or "(no disponible)",
            "url": vid_url,
            "duration": e.get("duration"),
            "extractor": e.get("ie_key"),
            "video_id": e.get("id"),
            "unavailable": not vid_url,
        })
    return {
        "title": info.get("title", "—"),
        "count": len(videos),
        "videos": videos,
    }

# --------------------------------------------------------------------------- #
# Watch mode — channel check
# --------------------------------------------------------------------------- #
def _check_channel_watch(state: AppState, channel: dict[str, Any]) -> list[dict[str, Any]]:
    """Revisa un canal por videos nuevos (solo deteccion, sin crear jobs).

    Usa ``extract_flat=True`` (rapido, sin bajar nada) y filtra contra
    ``downloaded_urls`` y jobs activos para no repetir.
    Devuelve una lista de videos nuevos con url, extractor, video_id y title.
    """
    url = channel["url"]
    try:
        info = _fetch_playlist(url)
    except Exception:
        log.exception("watch: error al leer playlist %s", _sanitize_url(url))
        return []

    new_videos: list[dict[str, Any]] = []
    for v in info.get("videos", []):
        extractor = v.get("extractor")
        video_id = str(v.get("video_id", ""))
        if not extractor or not video_id:
            continue
        if state.db.is_downloaded(extractor, video_id):
            continue
        if state.db.has_active_job_for_video(extractor, video_id):
            continue

        new_videos.append({
            "url": v["url"],
            "extractor": extractor,
            "video_id": video_id,
            "title": v.get("title", "?"),
        })

    return new_videos


# --------------------------------------------------------------------------- #
# Download job (runs in thread pool)
# --------------------------------------------------------------------------- #
def _run_download(state: AppState, job_id: str, url: str, quality: str, loop: asyncio.AbstractEventLoop) -> None:
    job = state.jobs[job_id]
    workdir = Path(tempfile.mkdtemp(prefix="opengrab_", dir=state.out_dir))
    max_size_mb, _ = state.resolve("max_size_mb", 0, int)
    job.workdir = str(workdir)
    evt = state.job_events.get(job_id)
    if evt is None:
        raise RuntimeError("job event no encontrado")

    def hook(d: Dict[str, Any]) -> None:
        if evt is None:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0.0
            job.status = "downloading"
            job.percent = round(pct, 1)
            job.speed = d.get("_speed_str", "").strip()
            job.eta = d.get("_eta_str", "").strip()
            job.downloaded = done
            job.total = total
            loop.call_soon_threadsafe(evt.set)
        elif status == "finished":
            job.status = "processing"
            job.percent = 100.0
            job.note = (
                "Extrayendo audio y convirtiendo a mp3…"
                if quality == "audio"
                else "Muxeando / remuxeando a mp4…"
            )
            loop.call_soon_threadsafe(evt.set)

    is_audio = quality == "audio"
    outtmpl = str(workdir / "%(title)s.%(ext)s")
    fmt = FORMATS.get(quality, FORMATS["best"])
    if max_size_mb and not is_audio:
        fmt += f"[filesize_approx<{max_size_mb}M]"
    opts: Dict[str, Any] = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "postprocessors": [],
        # Robustez universal: reintentos ante extractors/redes frágiles (sitios duros,
        # HLS fragmentado, rate limits). No es específico de ninguna plataforma.
        "extractor_retries": 3,
        "fragment_retries": 5,
        "retries": 5,
    }
    if is_audio:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
        opts.pop("merge_output_format", None)

    # En el binario de escritorio, ffmpeg viaja bundleado y no está en el PATH.
    # Guard: solo seteamos ffmpeg_location si el binario existe junto al recurso;
    # en Docker/dev no existe y yt-dlp usa el ffmpeg del PATH como siempre.
    _ffmpeg = resource_path("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if _ffmpeg.exists():
        opts["ffmpeg_location"] = str(_ffmpeg)

    try:
        job.status = "starting"
        job.percent = 0.0
        log.info(
            "job %s: iniciando descarga (%s, %s)",
            job_id, quality, _sanitize_url(url),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            raise RuntimeError("yt-dlp no devolvió información.")

        final = None
        requested = info.get("requested_downloads") or []
        if requested and requested[0].get("filepath"):
            final = Path(requested[0]["filepath"])

        if final is None:
            produced = sorted(
                workdir.glob("*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            produced = [p for p in produced if p.is_file()]
            if not produced:
                raise RuntimeError("No se generó ningún archivo.")
            final = produced[0]

        if not final.exists():
            raise RuntimeError(f"Archivo no encontrado: {final}")

        _enforce_size(final, max_size_mb)

        title = _safe_name(info.get("title", "video"))
        ext = final.suffix.lstrip(".")
        job.status = "done"
        job.percent = 100.0
        job.filepath = str(final)
        job.filename = f"{title}.{ext}"
        job.mime = "audio/mpeg" if is_audio else "video/mp4"
        job.title = title
        log.info("job %s: completado → %s", job_id, f"{title}.{ext}")
        state.complete_job(
            job_id,
            title=title,
            filename=f"{title}.{ext}",
            filepath=str(final),
            mime=job.mime,
            size=final.stat().st_size,
            thumbnail=info.get("thumbnail"),
        )
        extractor_key = info.get("extractor_key") or info.get("extractor")
        vid = info.get("id")
        if extractor_key and vid:
            state.db.update_job(job_id, extractor=extractor_key, video_id=str(vid))
            state.db.record_download(extractor_key, str(vid), job_id)
        loop.call_soon_threadsafe(evt.set)
    except Exception as exc:
        job.status = "error"
        job.error = _friendly_error(exc)
        # Persistir el error en la DB. Si no lo hacemos, un job manual que falla
        # queda 'queued' en SQLite (insert_job lo dejo asi y complete_job nunca corre),
        # y el dispatch_loop lo re-despacha cuando evict_once lo saca de memoria (~1h).
        try:
            state.db.update_job(job_id, status="error", error=job.error)
        except Exception:
            log.exception("job %s: no se pudo persistir estado 'error' en DB", job_id)
        loop.call_soon_threadsafe(evt.set)
        log.error("job %s: falló", job_id, exc_info=True)
    finally:
        loop.call_soon_threadsafe(evt.set)
