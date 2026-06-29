import socket


def _fake_gai(ip: str):
    """Factory: fake socket.getaddrinfo que resuelve cualquier host a `ip`."""
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET

    def _fake(host, port, *args, **kwargs):
        return [(fam, socket.SOCK_STREAM, 0, "", (ip, 0))]

    return _fake


def test_is_safe_url_accepts_any_public_site(monkeypatch):
    """Universal: cualquier http(s) publico pasa (yt-dlp decide si puede extraer)."""
    import download
    from download import _is_safe_url

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("93.184.216.34"))
    valid_urls = [
        # las plataformas conocidas siguen pasando
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://vimeo.com/123456789",
        "https://x.com/user/status/123456789",
        "https://www.tiktok.com/@user/video/123456789",
        "https://www.instagram.com/reel/abc123",
        # ...y ahora TAMBIEN cualquier otro sitio (navaja suiza)
        "https://example.com/watch?v=abc",
        "https://bandcamp.com/track/foo",
        "https://soundcloud.com/artist/track",
        "https://some-random-site.tv/video/42",
        "http://un-sitio-sin-tls.com/v/1",  # http tambien es valido
        "https://vimeo.com/",  # estructura incompleta: yt-dlp fallara, no el gate
        "https://www.youtube.com",  # dominio pelado: idem
    ]
    for url in valid_urls:
        assert _is_safe_url(url)[0], f"Deberia aceptar: {url}"


def test_is_safe_url_blocks_ssrf_and_bad_schemes():
    """Restrictivo en destino: bloquea SSRF (IPs literales) y esquemas no-http.

    Ninguna de estas URLs llega a resolver DNS (IP literal, host bloqueado,
    .local o malformada), asi que no hace falta mockear getaddrinfo.
    """
    from download import _is_safe_url

    blocked = [
        "file:///etc/passwd",          # LFI
        "ftp://internal/file",         # esquema no-http
        "javascript:alert(1)",
        "data:text/html,x",
        "http://localhost/admin",      # loopback por nombre
        "http://127.0.0.1:8800/",      # loopback
        "http://[::1]/",               # loopback IPv6
        "http://10.0.0.5/secret",      # privada
        "http://192.168.1.1/",         # privada
        "http://172.16.0.1/",          # privada
        "http://169.254.169.254/latest/meta-data/",  # metadata cloud (link-local)
        "http://server.local/x",       # .local
        "not a url",
        "",
        "https://",                    # sin host
    ]
    for url in blocked:
        assert not _is_safe_url(url)[0], f"Deberia bloquear: {url}"


def test_is_safe_url_blocks_domain_resolving_to_private(monkeypatch):
    """SSRF por DNS: dominio cuyo registro A apunta a IP privada se bloquea."""
    import download
    from download import _is_safe_url

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("10.0.0.5"))
    safe, reason = _is_safe_url("http://evil.attacker.com/")
    assert not safe
    assert reason == "error.url_private_ip"


def test_is_safe_url_allows_domain_resolving_to_public(monkeypatch):
    """Dominio cuyo A apunta a IP publica se permite."""
    import download
    from download import _is_safe_url

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("8.8.8.8"))
    safe, _reason = _is_safe_url("http://legit.example.com/")
    assert safe


def test_is_safe_url_blocks_on_dns_failure(monkeypatch):
    """Strict: si la resolucion DNS falla, bloquea (no fail-open) con mensaje propio."""
    import download
    from download import _is_safe_url

    def _boom(host, port, *args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(download.socket, "getaddrinfo", _boom)
    safe, reason = _is_safe_url("http://nxdomain.invalid/")
    assert not safe
    assert reason == "error.url_no_host"


def test_is_safe_url_blocks_domain_resolving_to_ipv6_ula(monkeypatch):
    """ULA IPv6 (fc00::/7) cae en is_private; dominio que resuelve ahi se bloquea."""
    import download
    from download import _is_safe_url

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("fc00::1"))
    safe, _reason = _is_safe_url("http://internal-v6.example/")
    assert not safe


def test_is_safe_url_strips_whitespace(monkeypatch):
    import download
    from download import _is_safe_url

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("93.184.216.34"))
    assert _is_safe_url("  https://youtu.be/abc  ")[0]
    assert not _is_safe_url("  not-a-url  ")[0]


def test_safe_name():
    from download import _safe_name

    assert _safe_name("Hello World") == "Hello World"
    assert _safe_name("video.mp4") == "video.mp4"
    assert _safe_name("file/name:test") == "filenametest"
    assert _safe_name("") == "video"
    assert _safe_name("a" * 200) == "a" * 120


def test_is_safe_url_reasons_son_keys_i18n_traducibles(monkeypatch):
    """Regresion anti-leak: las razones de rechazo son keys i18n que existen
    en es y en, y traducen distinto (un usuario en ingles no recibe espanol)."""
    import json
    from pathlib import Path

    import download
    import i18n
    from download import _is_safe_url

    es = json.loads((Path("static/i18n/es.json")).read_text(encoding="utf-8"))
    en = json.loads((Path("static/i18n/en.json")).read_text(encoding="utf-8"))

    monkeypatch.setattr(download.socket, "getaddrinfo", _fake_gai("10.0.0.5"))
    cases = [
        "ftp://x",                 # error.url_non_http
        "not-a-url",               # error.url_non_http (sin host)
        "http://localhost",        # error.url_internal
        "http://10.0.0.1",         # error.url_internal (IP literal)
        "http://evil.example/",    # error.url_private_ip (resuelve a privada)
    ]
    for url in cases:
        safe, reason = _is_safe_url(url)
        assert not safe, f"deberia rechazar {url}"
        assert reason.startswith("error."), f"reason no es key i18n: {reason!r}"
        assert reason in es and reason in en, f"key {reason!r} falta en algun idioma"
        # El fix real: traducir al ingles no devuelve el texto en espanol.
        assert i18n.t(reason, lang="en") == en[reason]
        assert i18n.t(reason, lang="en") != es[reason] or es[reason] == en[reason]
