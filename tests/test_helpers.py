def test_looks_like_youtube_valid():
    from app import _looks_like_youtube

    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
        "https://www.youtube.com/playlist?list=PLabc123",
        "https://www.youtube.com/embed/abc123",
    ]
    for url in valid_urls:
        assert _looks_like_youtube(url), f"Should match: {url}"


def test_looks_like_youtube_invalid():
    from app import _looks_like_youtube

    invalid_urls = [
        "https://vimeo.com/12345",
        "https://example.com/watch?v=abc",
        "not a url",
        "",
        "https://www.youtube.com",
        "https://youtube.com",
    ]
    for url in invalid_urls:
        assert not _looks_like_youtube(url), f"Should not match: {url}"


def test_safe_name():
    from app import _safe_name

    assert _safe_name("Hello World") == "Hello World"
    assert _safe_name("video.mp4") == "video.mp4"
    assert _safe_name("file/name:test") == "filenametest"
    assert _safe_name("") == "video"
    assert _safe_name("a" * 200) == "a" * 120  # capped at 120


def test_looks_like_youtube_strips():
    from app import _looks_like_youtube

    assert _looks_like_youtube("  https://youtu.be/abc  ")
    assert not _looks_like_youtube("  not-a-url  ")
