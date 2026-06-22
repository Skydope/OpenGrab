def test_is_safe_url_accepts_any_public_site():
    """Universal: cualquier http(s) publico pasa (yt-dlp decide si puede extraer)."""
    from download import _is_safe_url

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
        assert _is_safe_url(url), f"Deberia aceptar: {url}"


def test_is_safe_url_blocks_ssrf_and_bad_schemes():
    """Restrictivo en destino: bloquea SSRF y esquemas no-http (defensa en profundidad)."""
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
        assert not _is_safe_url(url), f"Deberia bloquear: {url}"


def test_safe_name():
    from download import _safe_name

    assert _safe_name("Hello World") == "Hello World"
    assert _safe_name("video.mp4") == "video.mp4"
    assert _safe_name("file/name:test") == "filenametest"
    assert _safe_name("") == "video"
    assert _safe_name("a" * 200) == "a" * 120


def test_is_safe_url_strips_whitespace():
    from download import _is_safe_url

    assert _is_safe_url("  https://youtu.be/abc  ")
    assert not _is_safe_url("  not-a-url  ")
