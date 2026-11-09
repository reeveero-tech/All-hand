"""D-145: تنزيل k6 المثبت يعيد المحاولة للأخطاء العابرة فقط (5xx و429 والمهلة).

السجل 397 §6: main ‏13ad8a61 فشل في مهمة test لأن التنزيل أعاد HTTP 504 ولا
إعادة محاولة. الأخطاء الدائمة (404 مثلًا) لا تُعاد، وبصمة SHA256 لا تتغير.
"""
import pathlib
import socket
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import r176_load_framework as fw  # noqa: E402


def _http(code):
    return urllib.error.HTTPError("https://x/k6.tgz", code, "err", {}, None)


def test_retries_transient_then_succeeds(tmp_path):
    calls = []

    def fake(url, dest):
        calls.append(url)
        if len(calls) < 3:
            raise _http(504)
        pathlib.Path(dest).write_bytes(b"ok")

    fw._download_with_retry("https://x/k6.tgz", tmp_path / "k6.tgz",
                            attempts=4, sleep=lambda s: None, fetch=fake)
    assert len(calls) == 3
    assert (tmp_path / "k6.tgz").read_bytes() == b"ok"


@pytest.mark.parametrize("exc", [_http(500), _http(502), _http(503), _http(429),
                                 socket.timeout("timed out"), TimeoutError("t")])
def test_gives_up_after_attempts_on_transient(tmp_path, exc):
    calls = []

    def fake(url, dest):
        calls.append(1)
        raise exc

    with pytest.raises(type(exc)):
        fw._download_with_retry("u", tmp_path / "f", attempts=3,
                                sleep=lambda s: None, fetch=fake)
    assert len(calls) == 3


@pytest.mark.parametrize("exc", [_http(404), _http(403), RuntimeError("network guard")])
def test_permanent_errors_are_not_retried(tmp_path, exc):
    calls = []

    def fake(url, dest):
        calls.append(1)
        raise exc

    with pytest.raises(type(exc)):
        fw._download_with_retry("u", tmp_path / "f", attempts=4,
                                sleep=lambda s: None, fetch=fake)
    assert len(calls) == 1


def test_ensure_k6_still_verifies_sha256():
    src = (pathlib.Path(fw.__file__)).read_text(encoding="utf-8")
    assert "k6 tarball SHA256 mismatch" in src
    assert "_download_with_retry(url, tgz)" in src
