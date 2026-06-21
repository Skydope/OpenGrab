def test_looks_like_supported_valid():
    from download import _looks_like_supported

    valid_urls = [
        # YouTube
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
        "https://www.youtube.com/playlist?list=PLabc123",
        "https://www.youtube.com/embed/abc123",
        # Vimeo
        "https://vimeo.com/123456789",
        "https://www.vimeo.com/123456789",
        # Twitter/X
        "https://twitter.com/user/status/123456789",
        "https://x.com/user/status/123456789",
        "https://www.x.com/user/status/123456789",
        # TikTok
        "https://tiktok.com/@user/video/123456789",
        "https://www.tiktok.com/@user/video/123456789",
        # Instagram
        "https://instagram.com/p/abc123",
        "https://www.instagram.com/reel/abc123",
        "https://www.instagram.com/tv/abc123",
    ]
    for url in valid_urls:
        assert _looks_like_supported(url), f"Should match: {url}"


def test_looks_like_supported_invalid():
    from download import _looks_like_supported

    invalid_urls = [
        "https://example.com/watch?v=abc",
        "https://vimeo.com/",  # no video id
        "https://tiktok.com/@user",  # no video id
        "not a url",
        "",
        "https://www.youtube.com",
        "https://youtube.com",
    ]
    for url in invalid_urls:
        assert not _looks_like_supported(url), f"Should not match: {url}"


def test_safe_name():
    from download import _safe_name

    assert _safe_name("Hello World") == "Hello World"
    assert _safe_name("video.mp4") == "video.mp4"
    assert _safe_name("file/name:test") == "filenametest"
    assert _safe_name("") == "video"
    assert _safe_name("a" * 200) == "a" * 120


def test_looks_like_supported_strips():
    from download import _looks_like_supported

    assert _looks_like_supported("  https://youtu.be/abc  ")
    assert not _looks_like_supported("  not-a-url  ")
