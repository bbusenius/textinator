"""Web UI tests: real HTTP against a server on an ephemeral port, with the
TTS backend mocked so nothing touches the network."""

import http.client
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

from textinator.feed import Feed
from textinator.webui import _App, _make_handler


@pytest.fixture
def server(tmp_path, dummy_backend, monkeypatch):
    monkeypatch.setattr(
        "textinator.webui.get_backend", lambda name: dummy_backend
    )
    feed_dir = tmp_path / "feed"
    feed = Feed.load(feed_dir)
    token = feed.ensure_token()
    feed.base_url = f"http://127.0.0.1:0/{token}"  # port patched below
    feed_dir.mkdir(parents=True)
    feed.save()

    app = _App(
        feed_dir=feed_dir,
        base_url=feed.base_url,
        page_url="http://127.0.0.1:0/",
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(app))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port, app, token
    httpd.shutdown()


def _request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if body is not None:
        body = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_form_page_renders(server):
    port, _app, _token = server
    status, body = _request(port, "GET", "/")
    assert status == 200
    assert b"<form" in body and b"Make episode" in body


def test_feed_link_is_clickable_with_qr(server):
    import segno

    port, app, _token = server
    status, body = _request(port, "GET", "/")
    assert status == 200
    page = body.decode()
    assert f'<a href="{app.base_url}/feed.xml">' in page
    # QR encodes the PAGE url (bootstrap another device), not the feed url —
    # the tappable feed link above covers subscribing
    expected = segno.make(app.page_url, error="m").svg_data_uri(scale=5)
    assert f'<img src="{expected}"' in page


def test_backend_choice_remembered_on_device(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    # localStorage script restores the saved backend and saves on submit
    assert "localStorage.getItem('textinator-backend')" in page
    assert "localStorage.setItem('textinator-backend'" in page


def test_jobs_endpoint_reports_progress(server):
    import json

    port, app, _token = server
    status, body = _request(port, "GET", "/jobs")
    assert status == 200
    assert json.loads(body) == []

    _request(
        port, "POST", "/make",
        body={"text": "Progress Note\n\nWatch me go.", "backend": "edge"},
    )
    for _ in range(100):
        _status, body = _request(port, "GET", "/jobs")
        jobs = json.loads(body)
        if jobs and jobs[0]["status"] == "done":
            break
        time.sleep(0.05)
    assert jobs[0]["status"] == "done"
    assert jobs[0]["title"] == "Progress Note"
    assert jobs[0]["detail"]  # duration + filename once finished


def test_job_list_polling_wired_into_page(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    assert '<ul id="jobs">' in page
    assert "fetch('/jobs')" in page
    assert "setInterval(refreshJobs" in page


def test_grok_auth_cost_labels_are_short(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    assert '<option value="grok">grok</option>' in page
    assert '<option value="edge">edge</option>' in page
    assert '<option value="kokoro">kokoro</option>' in page
    assert '<option value="oauth">OAuth · subscription</option>' in page
    assert '<option value="api">API key · metered</option>' in page
    assert '<option value="auto">' not in page
    assert "savedXaiAuth === 'oauth' || savedXaiAuth === 'api'" in page
    assert "$" not in page


def test_form_fields_persist_across_submits(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    # restore + save + clear wiring for each field
    for field in ("text", "url", "title", "voice"):
        assert f"textinator-field-{field}" in page or "'textinator-field-' + f.name" in page
    assert 'id="clear-form"' in page
    assert "localStorage.removeItem" in page
    assert "textinator-xai-auth" in page


def test_voice_field_shows_full_list_when_focused(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    # focus clears the value (so the datalist shows every option) and blur
    # restores it when nothing was picked or typed
    assert "voiceInput.addEventListener('focus'" in page
    assert "voiceInput.addEventListener('blur'" in page
    assert "this.dataset.prev = this.value" in page


def test_voice_hints_follow_backend(server):
    import json
    import re

    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    page = body.decode()
    # per-backend voice data is embedded as valid JSON, as {id, label} pairs
    hints = json.loads(re.search(r"var HINTS = (\{.*?\});", page).group(1))
    assert hints["edge"]["default"] == "en-US-GuyNeural"
    assert {"id": "af_heart", "label": "af_heart"} in hints["kokoro"]["voices"]
    assert [v["id"] for v in hints["grok"]["voices"]] == [
        "ara", "eve", "rex", "sal", "leo",
    ]
    # the datalist + updater are wired in
    assert '<datalist id="voice-options">' in page
    assert "updateVoiceHints" in page


def test_grok_custom_voices_merged_with_names(monkeypatch, tmp_path):
    from textinator.webui import build_voice_hints

    monkeypatch.setattr(
        "textinator.backends.grok.list_custom_voices",
        lambda: [{"voice_id": "cvtestid1234", "name": "Sample Voice"}],
    )
    hints = build_voice_hints(include_grok_custom=True)
    # cloned voice first, displayed by name, submitting the id
    assert hints["grok"]["voices"][0] == {
        "id": "cvtestid1234", "label": "Sample Voice (custom)",
    }
    # built-ins still present
    assert {"id": "eve", "label": "eve"} in hints["grok"]["voices"]


def test_grok_custom_voices_failure_is_silent(monkeypatch):
    from textinator.backends.grok import GrokError
    from textinator.webui import build_voice_hints

    def boom():
        raise GrokError("no key")

    monkeypatch.setattr("textinator.backends.grok.list_custom_voices", boom)
    hints = build_voice_hints(include_grok_custom=True)
    assert [v["id"] for v in hints["grok"]["voices"]] == [
        "ara", "eve", "rex", "sal", "leo",
    ]


def test_post_text_creates_episode(server):
    port, app, token = server
    status, _ = _request(
        port, "POST", "/make",
        body={"text": "Web Test Note\n\nHello from the web UI.", "backend": "edge"},
    )
    assert status == 303

    for _ in range(100):  # dummy backend is fast, but give it a moment
        with app.lock:
            job = app.jobs[0]
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done", job.detail
    assert job.title == "Web Test Note"

    # the feed the podcast app would poll now has the episode
    status, xml = _request(port, "GET", f"/{token}/feed.xml")
    assert status == 200
    assert b"Web Test Note" in xml


def test_post_empty_form_rejected(server):
    port, _app, _token = server
    status, _ = _request(port, "POST", "/make", body={"text": "", "url": ""})
    assert status == 400


def test_feed_files_require_token(server):
    port, app, token = server
    _request(
        port, "POST", "/make",
        body={"text": "Secret Note\n\nDo not leak.", "backend": "edge"},
    )
    for _ in range(100):
        with app.lock:
            if app.jobs and app.jobs[0].status == "done":
                break
        time.sleep(0.05)

    status, _ = _request(port, "GET", "/feed.xml")
    assert status == 404  # no token, no feed
    status, _ = _request(port, "GET", "/wrong-token/feed.xml")
    assert status == 404


def test_path_traversal_blocked(server):
    port, _app, token = server
    status, _ = _request(port, "GET", f"/{token}/../../../etc/passwd")
    assert status in (400, 404)


def test_episode_links_on_page_and_endpoint(server):
    import json

    port, app, token = server
    _request(
        port, "POST", "/make",
        body={"text": "Linked Note\n\nClick me later.", "backend": "edge"},
    )
    for _ in range(100):
        with app.lock:
            if app.jobs and app.jobs[0].status == "done":
                break
        time.sleep(0.05)

    # JSON endpoint: direct file link under the token path
    status, body = _request(port, "GET", "/episodes")
    assert status == 200
    episodes = json.loads(body)
    assert episodes[0]["title"] == "Linked Note"
    assert f"/{token}/episodes/" in episodes[0]["url"]
    assert episodes[0]["url"].endswith(".mp3")
    assert episodes[0]["duration"]

    # page render: episodes section with the same link
    _status, page = _request(port, "GET", "/")
    page = page.decode()
    assert '<ul id="episodes">' in page
    assert f'<a href="{episodes[0]["url"]}">Linked Note</a>' in page
    assert "fetch('/episodes')" in page  # live-refresh wired


def test_refresh_prunes_deleted_episodes(server):
    port, app, token = server
    # make two episodes
    for text in ("First Note\n\nKeep this one.", "Second Note\n\nDelete this one."):
        _request(port, "POST", "/make", body={"text": text, "backend": "edge"})
    for _ in range(200):
        with app.lock:
            if len(app.jobs) == 2 and all(j.status == "done" for j in app.jobs):
                break
        time.sleep(0.05)

    feed = Feed.load(app.feed_dir)
    victim = next(e for e in feed.episodes if e.title == "Second Note")
    (feed.episodes_dir / victim.filename).unlink()  # user deletes the audio

    status, _ = _request(port, "POST", "/refresh")
    assert status == 303

    _status, xml = _request(port, "GET", f"/{token}/feed.xml")
    assert b"First Note" in xml
    assert b"Second Note" not in xml
    with app.lock:
        entry = app.jobs[0]
    assert "removed 1" in entry.detail
    assert "Second Note" in entry.detail


def test_refresh_with_nothing_missing(server):
    port, app, _token = server
    status, _ = _request(port, "POST", "/refresh")
    assert status == 303
    with app.lock:
        assert app.jobs[0].detail == "nothing to remove"


def test_refresh_button_on_page(server):
    port, _app, _token = server
    _status, body = _request(port, "GET", "/")
    assert b'action="/refresh"' in body


def _multipart_body(fields: dict, file_field=None):
    """Build a multipart/form-data body. file_field = (name, filename, bytes)."""
    boundary = "testboundary123456"
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n".encode()
        )
    if file_field:
        name, filename, data = file_field
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
            + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _request_multipart(port, path, fields, file_field=None):
    body, content_type = _multipart_body(fields, file_field)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=body, headers={"Content-Type": content_type})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_epub_upload_makes_m4b_with_chapters(server, tmp_path):
    from ebooklib import epub as eb

    book = eb.EpubBook()
    book.set_identifier("upload-test")
    book.set_title("Uploaded Book")
    book.set_language("en")
    chs = []
    for i, t in enumerate(("Alpha", "Beta"), 1):
        ch = eb.EpubHtml(title=t, file_name=f"c{i}.xhtml", lang="en")
        ch.content = f"<html><body><h1>{t}</h1><p>Chapter {t} text here. " * 5 + "</p></body></html>"
        book.add_item(ch)
        chs.append(ch)
    book.toc = tuple(eb.Link(f"c{i}.xhtml", t, f"c{i}") for i, t in enumerate(("Alpha", "Beta"), 1))
    book.add_item(eb.EpubNcx())
    book.add_item(eb.EpubNav())
    book.spine = ["nav", *chs]
    path = tmp_path / "upload.epub"
    eb.write_epub(str(path), book)

    port, app, token = server
    status, _ = _request_multipart(
        port, "/make",
        {"backend": "edge", "voice": "", "title": "", "text": "", "url": ""},
        file_field=("file", "upload.epub", path.read_bytes()),
    )
    assert status == 303

    for _ in range(200):
        with app.lock:
            job = app.jobs[0]
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done", job.detail
    assert job.title == "Uploaded Book"
    assert ".m4b" in job.detail  # chapters -> m4b format

    _status, xml = _request(port, "GET", f"/{token}/feed.xml")
    assert b"Uploaded Book" in xml
    assert b'type="audio/mp4"' in xml


def test_text_file_upload(server):
    port, app, _token = server
    status, _ = _request_multipart(
        port, "/make",
        {"backend": "edge", "voice": "", "title": "", "text": "", "url": ""},
        file_field=("file", "my-notes.txt", b"Uploaded Note\n\nText file body."),
    )
    assert status == 303
    for _ in range(100):
        with app.lock:
            job = app.jobs[0]
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done", job.detail
    assert job.title == "Uploaded Note"


def test_multipart_post_without_file_still_works(server):
    port, app, _token = server
    status, _ = _request_multipart(
        port, "/make",
        {"backend": "edge", "text": "Multipart Text\n\nNo file attached.",
         "url": "", "voice": "", "title": ""},
    )
    assert status == 303
    for _ in range(100):
        with app.lock:
            job = app.jobs[0]
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done", job.detail


def test_oversized_upload_rejected(server, monkeypatch):
    import textinator.webui as webui_mod

    monkeypatch.setattr(webui_mod, "_MAX_UPLOAD_BYTES", 1024)
    port, _app, _token = server
    status, _ = _request_multipart(
        port, "/make",
        {"backend": "edge", "text": "", "url": "", "voice": "", "title": ""},
        file_field=("file", "big.pdf", b"x" * 4096),
    )
    assert status == 413


def test_failed_job_reports_error(server):
    port, app, _token = server
    status, _ = _request(
        port, "POST", "/make",
        body={"text": "```\nonly code, nothing speakable\n```", "backend": "edge"},
    )
    assert status == 303
    for _ in range(100):
        with app.lock:
            job = app.jobs[0]
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "failed"
    assert job.detail  # error surfaced to the UI
