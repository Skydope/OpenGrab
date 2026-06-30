"""Tests de la lógica pura del tray: ``_format_tray_status`` y ``_get_tray_image``.

No tocan pystray ni red: ``_format_tray_status`` es una función pura sobre la
lista que devuelve ``/api/jobs``, y ``_get_tray_image`` solo dibuja con PIL.
"""

from __future__ import annotations

import desktop


def _job(status: str, percent: float = 0.0, title: str = "",
         filename: str = "") -> dict[str, object]:
    return {"status": status, "percent": percent, "title": title, "filename": filename}


# --------------------------------------------------------------------------- #
# _format_tray_status
# --------------------------------------------------------------------------- #

class TestFormatTrayStatus:
    def test_inactivo_sin_jobs(self) -> None:
        active, tooltip, estado = desktop._format_tray_status([])
        assert active is False
        assert tooltip == "OpenGrab - inactivo"
        assert estado == "Inactivo"

    def test_inactivo_solo_terminados(self) -> None:
        jobs = [_job("done"), _job("error"), _job("cancelled")]
        active, _tooltip, estado = desktop._format_tray_status(jobs)
        assert active is False
        assert estado == "Inactivo"

    def test_descargando_muestra_pct_y_titulo(self) -> None:
        jobs = [_job("downloading", percent=42.7, title="Mi Video")]
        active, tooltip, estado = desktop._format_tray_status(jobs)
        assert active is True
        assert "42%" in tooltip and "Mi Video" in tooltip
        assert estado == "Descargando 42% · Mi Video"

    def test_processing_cuenta_como_activo(self) -> None:
        jobs = [_job("processing", percent=99.0, title="Procesando")]
        active, _tooltip, estado = desktop._format_tray_status(jobs)
        assert active is True
        assert "99%" in estado

    def test_elige_el_de_mayor_avance(self) -> None:
        jobs = [
            _job("downloading", percent=10.0, title="Lento"),
            _job("downloading", percent=80.0, title="Rápido"),
        ]
        _active, tooltip, estado = desktop._format_tray_status(jobs)
        assert "80%" in tooltip and "Rápido" in tooltip
        # Con 2 activos, sufijo de cantidad restante.
        assert "(+1)" in estado

    def test_fallback_a_filename_si_no_hay_titulo(self) -> None:
        jobs = [_job("downloading", percent=5.0, filename="clip.mp4")]
        _active, tooltip, _estado = desktop._format_tray_status(jobs)
        assert "clip.mp4" in tooltip

    def test_titulo_largo_se_trunca(self) -> None:
        largo = "X" * 200
        jobs = [_job("downloading", percent=1.0, title=largo)]
        _active, tooltip, _estado = desktop._format_tray_status(jobs)
        assert len(tooltip) <= 120
        assert "…" in tooltip

    def test_en_cola_sin_activos(self) -> None:
        jobs = [_job("queued"), _job("starting")]
        active, tooltip, estado = desktop._format_tray_status(jobs)
        assert active is False
        assert estado == "En cola (2)"
        assert "cola" in tooltip

    def test_activo_tiene_prioridad_sobre_cola(self) -> None:
        jobs = [_job("queued"), _job("downloading", percent=50.0, title="T")]
        active, _tooltip, estado = desktop._format_tray_status(jobs)
        assert active is True
        assert "Descargando" in estado

    def test_tooltip_literales_son_latin1(self) -> None:
        """Los tooltips de estados sin título de usuario deben ser latin-1-safe
        (el backend X11 de pystray escribe WM_NAME como STRING, no UTF8_STRING)."""
        for jobs in ([], [_job("queued")]):
            _active, tooltip, _estado = desktop._format_tray_status(jobs)
            tooltip.encode("latin-1")  # no debe lanzar UnicodeEncodeError

    def test_tooltip_con_titulo_no_latin1_es_sanitizable(self) -> None:
        """Un título con emoji/CJK no debe poder romper icon.title: la
        sanitización latin-1 'replace' que aplica _poll_tray_status no lanza."""
        jobs = [_job("downloading", percent=12.0, title="动画 🎬 фильм")]
        _active, tooltip, _estado = desktop._format_tray_status(jobs)
        # Replica la transformación de _poll_tray_status antes de icon.title.
        safe = tooltip.encode("latin-1", "replace").decode("latin-1")
        safe.encode("latin-1")  # round-trip sin excepción
        assert "12%" in safe


# --------------------------------------------------------------------------- #
# _get_tray_image
# --------------------------------------------------------------------------- #

class TestGetTrayImage:
    def test_sin_punto_de_estado(self) -> None:
        img = desktop._get_tray_image()
        assert img.size == (64, 64)  # type: ignore[attr-defined]

    def test_punto_verde_y_rojo_difieren(self) -> None:
        activo = desktop._get_tray_image(True)
        inactivo = desktop._get_tray_image(False)
        # El pixel del punto (esquina abajo-derecha) debe cambiar de color.
        assert activo.getpixel((51, 51)) != inactivo.getpixel((51, 51))  # type: ignore[attr-defined]

    def test_activo_es_verdoso_inactivo_rojizo(self) -> None:
        activo = desktop._get_tray_image(True).getpixel((51, 51))  # type: ignore[attr-defined]
        inactivo = desktop._get_tray_image(False).getpixel((51, 51))  # type: ignore[attr-defined]
        # activo: G domina sobre R; inactivo: R domina sobre G.
        assert activo[1] > activo[0]
        assert inactivo[0] > inactivo[1]
