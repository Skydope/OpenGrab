from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp  # type: ignore[import-untyped]
from yt_dlp.utils import DownloadCancelled  # type: ignore[import-untyped]

from config import FORMATS, IS_DESKTOP, resource_path
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


def _is_ip_unsafe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True si la IP es un destino que NO debe alcanzarse (anti-SSRF).

    `is_private` ya cubre IPv4-mapped IPv6 (``::ffff:10.0.0.5``) y las ULA
    IPv6 (``fc00::/7``) en CPython 3.12+, asi que no hace falta tratarlas
    aparte.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_hostname(hostname: str) -> tuple[bool, str]:
    """Resuelve un hostname y valida TODAS las IPs (A + AAAA) contra _is_ip_unsafe.

    Devuelve ``(safe, reason)`` donde ``reason`` es una *key i18n* (no texto
    literal): el caller la pasa por ``t()`` para traducirla. Politica strict:
    - Si la resolucion falla (`gaierror`), bloquea: preferimos un falso
      negativo transitorio a un bypass silencioso de SSRF.
    - Si CUALQUIER IP resuelta es insegura, bloquea: un atacante podria
      mezclar una IP publica con una privada en registros round-robin, y
      yt-dlp podria conectar a cualquiera.

    NOTA: queda un TOCTOU residual entre esta resolucion y la propia de
    yt-dlp (DNS rebinding). Eso se cierra en la capa de red (egress
    filtering en DOCKER-USER), no aca.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
    except socket.gaierror:
        return False, "error.url_no_host"
    except (UnicodeError, OSError):
        return False, "error.url_invalid_host"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, "error.url_invalid_host"
        if _is_ip_unsafe(ip):
            return False, "error.url_private_ip"
    return True, ""


def _is_safe_url(url: str) -> tuple[bool, str]:
    """``(safe, reason)`` — True si la URL es http(s) publica segura para yt-dlp.

    ``reason`` es una *key i18n* (p.ej. ``error.url_internal``), no texto
    literal: el caller la traduce con ``t()`` segun el idioma del request.

    Universal en cuanto a sitio; restrictivo en cuanto a destino (anti-SSRF).
    Resuelve DNS y valida todas las IPs: bloquea dominios que apuntan a rangos
    privados, loopback, link-local (incl. metadata ``169.254.169.254``) y ULA
    IPv6, no solo IPs literales en la URL.
    """
    try:
        parsed = urlparse(url.strip())
        host = parsed.hostname
    except ValueError:
        return False, "error.url_invalid"
    if parsed.scheme not in ("http", "https") or not host:
        return False, "error.url_non_http"
    host_l = host.lower()
    if host_l in _BLOCKED_HOSTS or host_l.endswith((".local", ".localhost")):
        return False, "error.url_internal"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname de dominio: resolver y validar todas las IPs.
        return _resolve_hostname(host)
    # IP literal en la URL: validar directo, sin resolver.
    if _is_ip_unsafe(ip):
        return False, "error.url_internal"
    return True, ""


def _enforce_size(path: Path, max_mb: int) -> None:
    """Borra y falla si el archivo final supera el limite por-archivo.
    Cubre el caso donde filesize_approx subestimo o el filtro no aplico (audio)."""
    if max_mb and path.stat().st_size > max_mb * 1024 * 1024:
        try:
            path.unlink()
        except OSError:
            pass
        from i18n import t
        raise RuntimeError(
            t("error.size_exceeded", max_mb=max_mb)
        )


def _safe_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name)
    name = re.sub(r"[^\w \t.\-()\[\]]", "", name, flags=re.UNICODE).strip()
    return (name or "video")[:120]


def _sanitize_url(url: str) -> str:
    clean = re.sub(r"([?&]token=)[^&]+", r"\1***", url)
    clean = clean.replace("\n", "").replace("\r", "")
    return clean[:200]


# (patrón en minúsculas → key i18n). El primero que matchee gana.
_ERROR_MAP: list[tuple[str, str]] = [
    ("403", "error.yt_403"),
    ("404", "error.yt_404"),
    ("private video", "error.yt_private"),
    ("members-only", "error.yt_members_only"),
    ("sign in to confirm your age", "error.yt_age"),
    ("age", "error.yt_age"),
    ("sign in", "error.yt_sign_in"),
    ("not available in your country", "error.yt_geo"),
    ("geo", "error.yt_geo"),
    ("video unavailable", "error.yt_unavailable"),
    ("is not available", "error.yt_unavailable"),
    ("unsupported url", "error.url_unsupported"),
    ("ffmpeg", "error.ffmpeg"),
    ("ffprobe", "error.ffmpeg"),
    ("timed out", "error.network_timeout"),
    ("connection", "error.network"),
    ("urlopen error", "error.network"),
]


def _friendly_error(exc: Exception) -> str:
    """Traduce errores técnicos de yt-dlp a mensajes que un humano entiende.

    Los mensajes internos (RuntimeError con ``t()`` ya aplicado) se devuelven
    tal cual; lo desconocido cae al texto crudo recortado."""
    from i18n import t

    raw = str(exc)
    low = raw.lower()
    for needle, i18n_key in _ERROR_MAP:
        if needle in low:
            return t(i18n_key)
    return raw[:300]


# --------------------------------------------------------------------------- #
# yt-dlp wrappers
# --------------------------------------------------------------------------- #
def _fetch_info(url: str) -> dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        from i18n import t
        raise RuntimeError(t("error.ytdl_no_info"))
    return info  # type: ignore[no-any-return]


def _fetch_playlist(url: str) -> dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        # "in_playlist" aplana SOLO el primer nivel: para watch?v=...&list=...
        # yt-dlp devuelve _type:"playlist" con sus entries. Con True (aplanado
        # recursivo) resuelve la URL al video y entrega entries=None -> 0/0.
        "extract_flat": "in_playlist",
        "skip_download": True,
        # Un video privado/borrado no debe abortar el aplanado de toda la lista.
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        from i18n import t
        raise RuntimeError(t("error.ytdl_no_info"))
    # entries puede ser un generador perezoso (LazyList): materializarlo.
    entries = list(info.get("entries") or [])
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
def _run_download(state: AppState, job_id: str, url: str, quality: str,
                  loop: asyncio.AbstractEventLoop,
                  subs: bool = False, thumb: bool = False,
                  infojson: bool = False) -> None:
    job = state.jobs[job_id]
    workdir = Path(tempfile.mkdtemp(prefix="opengrab_", dir=state.out_dir))
    max_size_mb, _ = state.resolve("max_size_mb", 0, int)
    job.workdir = str(workdir)
    evt = state.job_events.get(job_id)
    if evt is None:
        from i18n import t
        raise RuntimeError(t("error.job_not_found_short"))

    def hook(d: dict[str, Any]) -> None:
        if evt is None:
            return
        if job_id in state.cancel_requests:
            # Abortar yt-dlp desde el hook: la excepción propaga fuera de
            # extract_info y la captura el except DownloadCancelled de abajo.
            from i18n import t
            raise DownloadCancelled(t("error.cancelled_by_user"))
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
            from i18n import t as _t
            job.note = (
                _t("download.extracting_audio")
                if quality == "audio"
                else _t("download.muxing")
            )
            loop.call_soon_threadsafe(evt.set)

    is_audio = quality == "audio"
    outtmpl = str(workdir / "%(title)s.%(ext)s")
    fmt = FORMATS.get(quality, FORMATS["best"])
    if max_size_mb and not is_audio:
        fmt += f"[filesize_approx<{max_size_mb}M]"
    opts: dict[str, Any] = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "postprocessors": [],
        "socket_timeout": 30,
        # Reintentos para extractors/redes frágiles (HLS fragmentado, rate limits).
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

    if subs:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["es", "en"]
    if thumb:
        opts["writethumbnail"] = True
    if infojson:
        opts["writeinfojson"] = True

    # En el binario de escritorio, ffmpeg viaja bundleado y no está en el PATH.
    # Guard: solo seteamos ffmpeg_location si el binario existe junto al recurso;
    # en Docker/dev no existe y yt-dlp usa el ffmpeg del PATH como siempre.
    _ffmpeg = resource_path("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if _ffmpeg.exists():
        opts["ffmpeg_location"] = str(_ffmpeg)

    try:
        job.status = "starting"
        job.percent = 0.0
        if job_id in state.cancel_requests:
            from i18n import t
            raise DownloadCancelled(t("error.cancelled_before_start"))
        log.info(
            "job %s: iniciando descarga (%s, %s)",
            job_id, quality, _sanitize_url(url),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            from i18n import t
            raise RuntimeError(t("error.ytdl_no_info"))

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
                from i18n import t
                raise RuntimeError(t("error.no_file_generated"))
            final = produced[0]

        if not final.exists():
            from i18n import t
            raise RuntimeError(t("error.file_not_found", final=str(final)))

        _enforce_size(final, max_size_mb)

        # Desktop finalize: mueve a library_dir si corresponde
        state._finalize_desktop(job_id, workdir, final, info, quality)

        # Server mode: move file out of temp workdir to OUT_DIR and clean up.
        # Each job gets its own tempdir; we clean it as soon as this download
        # finishes so no orphaned folders accumulate.
        if not IS_DESKTOP and final.parent == workdir:
            dest = state.out_dir / final.name
            dest = state._deduplicate(dest)
            shutil.move(str(final), str(dest))
            final = dest
            state._schedule_tempdir_cleanup(str(workdir))
            job.workdir = ""  # already cleaned

        # Usar filepath actualizado por _finalize_desktop (puede haber cambiado)
        title = _safe_name(info.get("title", "video"))
        ext = final.suffix.lstrip(".") or ("mp3" if is_audio else "mp4")
        mime = "audio/mpeg" if is_audio else "video/mp4"
        job.status = "done"
        job.finished = time.time()
        job.percent = 100.0
        job.filepath = job.filepath or str(final)
        job.filename = (job.filepath and Path(job.filepath).name) or f"{title}.{ext}"
        # Desktop: si el finalize movió el archivo a library_dir, el husk del
        # workdir es descartable. Mode-agnostic y seguro ante finalize fallido
        # (si el keeper sigue dentro del workdir, esto es no-op). En server mode
        # ya se limpió arriba (job.workdir == ""), así que también es no-op.
        state.schedule_workdir_if_external(job)
        job.mime = mime
        job.title = title
        log.info("job %s: completado → %s", job_id, job.filepath)
        state.complete_job(
            job_id,
            title=title,
            filename=job.filename or f"{title}.{ext}",
            filepath=job.filepath,
            mime=job.mime,
            size=Path(job.filepath).stat().st_size if job.filepath else 0,
            thumbnail=info.get("thumbnail"),
        )
        extractor_key = info.get("extractor_key") or info.get("extractor")
        vid = info.get("id")
        if extractor_key and vid:
            state.db.update_job(job_id, extractor=extractor_key, video_id=str(vid))
            state.db.record_download(extractor_key, str(vid), job_id)
        loop.call_soon_threadsafe(evt.set)
    except DownloadCancelled:
        job.status = "cancelled"
        job.finished = time.time()
        job.error = ""
        # El archivo final nunca se movió afuera: el workdir es un husk con
        # descargas parciales. Registrarlo para limpieza (sin keeper adentro).
        state._schedule_tempdir_cleanup(str(workdir))
        job.workdir = ""
        try:
            state.db.update_job(job_id, status="cancelled")
        except Exception:
            log.exception("job %s: no se pudo persistir estado 'cancelled'", job_id)
        loop.call_soon_threadsafe(evt.set)
        log.info("job %s: cancelado", job_id)
    except Exception as exc:
        job.status = "error"
        job.finished = time.time()
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
        state.cancel_requests.discard(job_id)
        loop.call_soon_threadsafe(evt.set)
