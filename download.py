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
from dataclasses import dataclass
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

# User-Agent genérico de navegador para modo incógnito. El default de yt-dlp es
# reconocible como herramienta; este lo enmascara como Chrome. NOTA DE
# MANTENIMIENTO: la versión (Chrome/xxx) envejece — si sitios empiezan a rechazar
# UAs viejos, actualizar acá. Idealmente sincronizar con el bump de yt-dlp
# (mismo cadence que la CI de frescura del engine).
_INCOGNITO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


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

@dataclass(frozen=True)
class DownloadContext:
    """Parámetros de negocio inmutables para un job de descarga."""
    job_id: str
    url: str
    quality: str
    subs: bool = False
    thumb: bool = False
    infojson: bool = False
    incognito: bool = False
    incognito_dir: str | None = None
    playlist_subdir: str | None = None


def _handle_incognito_completion(
    state: AppState, ctx: DownloadContext,
    loop: asyncio.AbstractEventLoop, evt: asyncio.Event,
    job: Any, workdir: Path, final: Path, title: str, mime: str,
) -> None:
    """Post-descarga incógnito: move + wipe + delete DB row."""
    assert ctx.incognito_dir, "incognito_dir requerido en modo incógnito"
    try:
        delivered = state._move_incognito(final, Path(ctx.incognito_dir))
    except OSError as move_exc:
        from i18n import t
        job.status = "error"
        job.finished = time.time()
        job.error = t("error.incognito_move_failed", path=str(final))
        job.filepath = str(final)
        try:
            state.db.delete_job(ctx.job_id)
        except Exception:
            log.exception("job %s: no se pudo borrar fila incógnito de DB", ctx.job_id)
        log.error(
            "job %s: descarga incógnito completa pero falló el move; "
            "archivo preservado para recuperación manual en %s (%s)",
            ctx.job_id, final, move_exc,
        )
        loop.call_soon_threadsafe(evt.set)
        return
    job.status = "done"
    job.finished = time.time()
    job.percent = 100.0
    job.filepath = str(delivered)
    job.filename = delivered.name
    job.mime = mime
    job.title = title
    try:
        state._secure_delete_workdir(str(workdir), force=True)
    except OSError:
        log.warning("job %s: no se pudo wipear workdir incógnito", ctx.job_id)
    job.workdir = ""
    try:
        state.db.delete_job(ctx.job_id)
    except Exception:
        log.exception("job %s: no se pudo borrar fila incógnito de DB", ctx.job_id)
    log.info("job %s: completado (incógnito, sin historial)", ctx.job_id)
    loop.call_soon_threadsafe(evt.set)


def _finalize_download(
    state: AppState, ctx: DownloadContext,
    loop: asyncio.AbstractEventLoop, evt: asyncio.Event,
    job: Any, workdir: Path, final: Path, info: dict[str, Any],
    title: str, ext: str, mime: str,
) -> None:
    """Post-descarga normal: desktop finalize + server move + DB persist."""
    state._finalize_desktop(ctx.job_id, workdir, final, info, ctx.quality, ctx.playlist_subdir)

    if not IS_DESKTOP and final.parent == workdir:
        dest_dir = state.out_dir / ctx.playlist_subdir if ctx.playlist_subdir else state.out_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = state._deduplicate(dest_dir / final.name)
        shutil.move(str(final), str(dest))
        final = dest
        state._schedule_tempdir_cleanup(str(workdir))
        job.workdir = ""

    job.status = "done"
    job.finished = time.time()
    job.percent = 100.0
    job.filepath = job.filepath or str(final)
    job.filename = (job.filepath and Path(job.filepath).name) or f"{title}.{ext}"
    state.schedule_workdir_if_external(job)
    job.mime = mime
    job.title = title
    log.info("job %s: completado → %s", ctx.job_id, job.filepath)
    state.complete_job(
        ctx.job_id,
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
        state.db.update_job(ctx.job_id, extractor=extractor_key, video_id=str(vid))
        state.db.record_download(extractor_key, str(vid), ctx.job_id)
    loop.call_soon_threadsafe(evt.set)


def _handle_termination(
    state: AppState, ctx: DownloadContext,
    loop: asyncio.AbstractEventLoop, evt: asyncio.Event,
    job: Any, workdir: Path,
    is_cancelled: bool, exc: Exception | None = None,
) -> None:
    """Cancelación y error: cleanup + DB, con bifurcación incógnito."""
    if is_cancelled:
        job.status = "cancelled"
        job.finished = time.time()
        job.error = ""
        if ctx.incognito:
            try:
                state._secure_delete_workdir(str(workdir), force=True)
            except OSError:
                log.warning("job %s: no se pudo wipear workdir incógnito", ctx.job_id)
            job.workdir = ""
            try:
                state.db.delete_job(ctx.job_id)
            except Exception:
                log.exception("job %s: no se pudo borrar fila incógnito de DB", ctx.job_id)
            log.info("job %s: cancelado (incógnito)", ctx.job_id)
        else:
            state._schedule_tempdir_cleanup(str(workdir))
            job.workdir = ""
            try:
                state.db.update_job(ctx.job_id, status="cancelled")
            except Exception:
                log.exception("job %s: no se pudo persistir estado 'cancelled'", ctx.job_id)
            log.info("job %s: cancelado", ctx.job_id)
    else:
        job.status = "error"
        job.finished = time.time()
        job.error = _friendly_error(exc)  # type: ignore[arg-type]
        if ctx.incognito:
            try:
                state._secure_delete_workdir(str(workdir), force=True)
            except OSError:
                log.warning("job %s: no se pudo wipear workdir incógnito", ctx.job_id)
            job.workdir = ""
            try:
                state.db.delete_job(ctx.job_id)
            except Exception:
                log.exception("job %s: no se pudo borrar fila incógnito de DB", ctx.job_id)
            log.error("job %s: falló (incógnito)", ctx.job_id, exc_info=True)
        else:
            try:
                state.db.update_job(ctx.job_id, status="error", error=job.error)
            except Exception:
                log.exception("job %s: no se pudo persistir estado 'error' en DB", ctx.job_id)
            log.error("job %s: falló", ctx.job_id, exc_info=True)
    loop.call_soon_threadsafe(evt.set)


def _build_ydl_opts(ctx: DownloadContext, workdir: Path,
                    max_size_mb: int, hook: Any) -> dict[str, Any]:
    is_audio = ctx.quality == "audio"
    outtmpl = str(workdir / "%(title)s.%(ext)s")
    fmt = FORMATS.get(ctx.quality, FORMATS["best"])
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

    # Sidecars: forzados off si incógnito (ctx es frozen, no podemos mutarlo).
    subs = False if ctx.incognito else ctx.subs
    thumb = False if ctx.incognito else ctx.thumb
    infojson = False if ctx.incognito else ctx.infojson

    if subs:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["es", "en"]
    if thumb:
        opts["writethumbnail"] = True
    if infojson:
        opts["writeinfojson"] = True

    # Hardening de privacidad para modo incógnito: sin caché en disco (evita
    # ~/.cache/yt-dlp con rastros de qué se consultó) y User-Agent genérico de
    # navegador en lugar del default de yt-dlp (que delata la herramienta).
    if ctx.incognito:
        opts["cachedir"] = False
        opts["http_headers"] = {"User-Agent": _INCOGNITO_USER_AGENT}

    # En el binario de escritorio, ffmpeg viaja bundleado y no está en el PATH.
    # Guard: solo seteamos ffmpeg_location si el binario existe junto al recurso;
    # en Docker/dev no existe y yt-dlp usa el ffmpeg del PATH como siempre.
    _ffmpeg = resource_path("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if _ffmpeg.exists():
        opts["ffmpeg_location"] = str(_ffmpeg)

    return opts


def _run_download(state: AppState, ctx: DownloadContext,
                  loop: asyncio.AbstractEventLoop) -> None:
    job = state.jobs[ctx.job_id]
    workdir = Path(tempfile.mkdtemp(prefix="opengrab_", dir=state.out_dir))
    max_size_mb, _ = state.resolve("max_size_mb", 0, int)
    job.workdir = str(workdir)
    evt = state.job_events.get(ctx.job_id)
    if evt is None:
        from i18n import t
        raise RuntimeError(t("error.job_not_found_short"))

    def hook(d: dict[str, Any]) -> None:
        if evt is None:
            return
        if ctx.job_id in state.cancel_requests:
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
                if ctx.quality == "audio"
                else _t("download.muxing")
            )
            loop.call_soon_threadsafe(evt.set)

    opts = _build_ydl_opts(ctx, workdir, max_size_mb, hook)

    try:
        job.status = "starting"
        job.percent = 0.0
        if ctx.job_id in state.cancel_requests:
            from i18n import t
            raise DownloadCancelled(t("error.cancelled_before_start"))
        if ctx.incognito:
            log.info("job %s: iniciando descarga incógnito (%s)", ctx.job_id, ctx.quality)
        else:
            log.info(
                "job %s: iniciando descarga (%s, %s)",
                ctx.job_id, ctx.quality, _sanitize_url(ctx.url),
            )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(ctx.url, download=True)

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

        is_audio = ctx.quality == "audio"
        title = _safe_name(info.get("title", "video"))
        ext = final.suffix.lstrip(".") or ("mp3" if is_audio else "mp4")
        mime = "audio/mpeg" if is_audio else "video/mp4"

        if ctx.incognito:
            _handle_incognito_completion(state, ctx, loop, evt, job, workdir, final, title, mime)
            return

        # Desktop finalize + server move + DB persist
        _finalize_download(state, ctx, loop, evt, job, workdir, final, info, title, ext, mime)
    except DownloadCancelled:
        _handle_termination(state, ctx, loop, evt, job, workdir, is_cancelled=True)
    except Exception as exc:
        _handle_termination(state, ctx, loop, evt, job, workdir, is_cancelled=False, exc=exc)
    finally:
        state.cancel_requests.discard(ctx.job_id)
        loop.call_soon_threadsafe(evt.set)
