"""Tests del clasificador de frescura del motor (scripts/engine_smoke.py).

Solo se testea la función pura ``classify`` — sin red. El sondeo real
(``_probe``/``main``) vive en el workflow programado, no en el gate.
"""

from engine_smoke import (
    EXIT_BROKEN,
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    classify,
)


def test_classify_healthy():
    info = {"title": "Me at the zoo", "formats": [{"format_id": "18"}]}
    assert classify(info, None) == ("healthy", EXIT_HEALTHY)


def test_classify_broken_when_no_formats():
    info = {"title": "algo", "formats": []}
    assert classify(info, None) == ("broken", EXIT_BROKEN)


def test_classify_broken_when_no_title():
    info = {"title": "", "formats": [{"format_id": "18"}]}
    assert classify(info, None) == ("broken", EXIT_BROKEN)


def test_classify_broken_when_info_none_and_no_error():
    assert classify(None, None) == ("broken", EXIT_BROKEN)


def test_classify_broken_on_extractor_error():
    err = "ExtractorError: Unable to extract player response"
    assert classify(None, err) == ("broken", EXIT_BROKEN)


def test_classify_unavailable_on_bot_check():
    err = "DownloadError: Sign in to confirm you're not a bot"
    assert classify(None, err) == ("unavailable", EXIT_UNAVAILABLE)


def test_classify_unavailable_on_429():
    err = "DownloadError: HTTP Error 429: Too Many Requests"
    assert classify(None, err) == ("unavailable", EXIT_UNAVAILABLE)


def test_classify_unavailable_on_dns_failure():
    err = "URLError: <urlopen error [Errno -3] Temporary failure in name resolution>"
    assert classify(None, err) == ("unavailable", EXIT_UNAVAILABLE)


def test_classify_unavailable_is_case_insensitive():
    err = "DownloadError: SIGN IN TO CONFIRM you're not a bot"
    assert classify(None, err) == ("unavailable", EXIT_UNAVAILABLE)


def test_classify_unavailable_on_ssl_error():
    err = "DownloadError: Unable to download API page: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    assert classify(None, err) == ("unavailable", EXIT_UNAVAILABLE)


def test_classify_broken_on_bare_download_failure():
    # "unable to download API page" SIN marcador de bloqueo/SSL → fetch roto = alerta.
    err = "DownloadError: Unable to download API page: HTTP Error 400: Bad Request"
    assert classify(None, err) == ("broken", EXIT_BROKEN)


def test_classify_error_takes_precedence_over_info():
    # Si hubo error, no miramos info (que vendría vacío de todos modos).
    assert classify({"title": "x", "formats": [1]}, "boom unable to extract")[0] == "broken"
