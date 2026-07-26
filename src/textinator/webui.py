"""Tiny local web UI: paste text or a URL from any device on the LAN.

One stdlib server does both jobs:
- ``GET /``            the paste form + job list (no auth — home LAN only)
- ``POST /make``       start a generation job in a background thread
- ``/<token>/...``     the feed directory (feed.xml + episodes), so the same
                       process hosts the private feed your podcast app polls.

Generation runs in a worker thread; the page shows job status so a phone
browser never sits on a multi-minute request.
"""

from __future__ import annotations

import html
import tempfile
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .backends import AVAILABLE_BACKENDS, get_backend
from .backends.grok import VOICES as GROK_VOICES
from .backends.kokoro import KNOWN_VOICES as KOKORO_VOICES

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # protect the little server


def _load_upload(path: Path, original_name: str):
    """Route an uploaded file to the right input adapter by its extension."""
    suffix = Path(original_name).suffix.lower()
    stem = Path(original_name).stem
    if suffix == ".epub":
        from .inputs import epub as epub_input

        document = epub_input.load(str(path))
    elif suffix == ".pdf":
        from .inputs import pdf as pdf_input

        document = pdf_input.load(str(path))
    else:
        from .inputs import Document
        from .inputs.paste import derive_title

        text = path.read_text(encoding="utf-8", errors="replace")
        return Document(title=derive_title(text, fallback=stem), text=text)
    # the temp file's random stem is a useless title fallback
    if document.title == path.stem:
        document.title = stem
    return document


def _episode_entries(app: "_App") -> list[dict]:
    """Episodes as JSON-able dicts, newest first, with direct file links."""
    from .feed import _format_duration

    feed = Feed.load(app.feed_dir)
    entries = []
    for episode in sorted(feed.episodes, key=lambda e: e.pub_date, reverse=True):
        entries.append(
            {
                "title": episode.title,
                "url": feed.enclosure_url(episode),
                "duration": _format_duration(episode.duration_seconds),
                "date": episode.pub_date[:10],
            }
        )
    return entries


def _backend_label(name: str) -> str:
    return name
from .deliver import lan_ip
from .feed import Feed
from .inputs import paste as paste_input
from .inputs import url as url_input
from . import pipeline

# per-backend voice suggestions for the form (edge has hundreds of voices,
# so it gets a curated English set; `textinator --list-voices` shows them all)
_EDGE_SAMPLE_VOICES = (
    "en-US-GuyNeural", "en-US-AriaNeural", "en-US-JennyNeural",
    "en-US-ChristopherNeural", "en-US-MichelleNeural",
    "en-GB-RyanNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural",
)


def build_voice_hints(include_grok_custom: bool = False) -> dict:
    """Voice suggestions per backend as {id, label} entries.

    With ``include_grok_custom``, the team's cloned voices are fetched from
    the xAI API (free metadata call) and listed by name — best-effort: no
    key or no network just means they don't appear.
    """

    def plain(voices):
        return [{"id": v, "label": v} for v in voices]

    hints = {
        "edge": {"default": "en-US-GuyNeural", "voices": plain(_EDGE_SAMPLE_VOICES)},
        "kokoro": {"default": "af_heart", "voices": plain(KOKORO_VOICES)},
        "grok": {"default": "eve", "voices": plain(GROK_VOICES)},
    }
    if include_grok_custom:
        try:
            from .backends.grok import list_custom_voices

            custom = [
                {
                    "id": v["voice_id"],
                    "label": f"{v.get('name') or v['voice_id']} (custom)",
                }
                for v in list_custom_voices()
                if v.get("voice_id")
            ]
            # cloned voices first — if you made one, it's the one you want
            hints["grok"]["voices"] = custom + hints["grok"]["voices"]
        except Exception:
            pass  # decoration only; the form works without it
    return hints

_MIME = {
    ".xml": "application/rss+xml; charset=utf-8",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
}


@dataclass
class Job:
    title: str
    status: str = "running"  # running | done | failed
    detail: str = ""
    started: str = field(
        default_factory=lambda: datetime.now().strftime("%H:%M:%S")
    )


class _App:
    """Shared state between request threads."""

    def __init__(
        self,
        feed_dir: Path,
        base_url: str,
        page_url: str = "",
        voice_hints: dict | None = None,
    ):
        self.feed_dir = feed_dir
        self.base_url = base_url
        self.page_url = page_url
        self.voice_hints = voice_hints or build_voice_hints()
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        self._page_qr: str = ""  # data URI, built once (URL is fixed per run)

    @property
    def page_qr(self) -> str:
        """QR for THIS page — scan to open the UI on another device; the
        tappable feed link is then right there on the page."""
        if not self._page_qr:
            import segno

            self._page_qr = segno.make(
                self.page_url or self.base_url, error="m"
            ).svg_data_uri(scale=5)
        return self._page_qr

    def start_job(
        self,
        source: str,
        backend_name: str,
        voice: str,
        title: str,
        xai_auth: str = "oauth",
        upload_path: Path | None = None,
        upload_name: str = "",
    ):
        job = Job(title=title or upload_name or source[:60] or "pasted text")
        with self.lock:
            self.jobs.insert(0, job)

        def work():
            try:
                if upload_path is not None:
                    job.detail = f"reading {upload_name}"
                    document = _load_upload(upload_path, upload_name)
                elif url_input.is_url(source):
                    job.detail = "fetching"
                    document = url_input.load(source)
                else:
                    document = paste_input.load_text(source)
                if title:
                    document.title = title
                job.title = document.title
                job.detail = "starting synthesis"
                backend = get_backend(backend_name)
                if backend.name == "grok":
                    backend.auth_mode = xai_auth
                    backend.prepare_auth()
                    job.detail = (
                        f"xAI auth: {backend.auth_label} · cost: {backend.cost_label}"
                    )
                result = pipeline.run(
                    document,
                    backend,
                    feed_dir=self.feed_dir,
                    voice=voice or None,
                    base_url=self.base_url,
                    # epubs (and anything else with structure) keep chapters
                    episode_format="m4b" if document.chapters else "mp3",
                    on_progress=lambda msg: setattr(job, "detail", msg),
                )
                minutes, seconds = divmod(int(result.duration_seconds), 60)
                job.status = "done"
                job.detail = f"{minutes}m{seconds:02d}s · {result.episode_path.name}"
            except Exception as exc:  # surfaced in the UI, not lost in a thread
                job.status = "failed"
                job.detail = str(exc) or exc.__class__.__name__
                traceback.print_exc()
            finally:
                if upload_path is not None:
                    upload_path.unlink(missing_ok=True)

        threading.Thread(target=work, daemon=True).start()


_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>textinator</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 1rem auto; padding: 0 1rem; }}
 textarea, input[type=text], select {{ width: 100%; box-sizing: border-box; margin: .25rem 0 .75rem; padding: .5rem; font-size: 1rem; }}
 textarea {{ height: 10rem; }}
 button {{ font-size: 1.1rem; padding: .5rem 1.5rem; }}
 li {{ margin: .3rem 0; }}
 .done {{ color: #2a7; }} .failed {{ color: #c33; }} .running {{ color: #888; }}
 code {{ background: #eee; padding: .1rem .3rem; border-radius: 3px; word-break: break-all; }}
</style></head><body>
<h1>textinator</h1>
<form method="post" action="/make" enctype="multipart/form-data">
 <label>Paste text (or use the URL / file fields below)</label>
 <textarea name="text" placeholder="Paste anything worth listening to…"></textarea>
 <label>…or URL (articles, or direct links to .pdf / .epub)</label>
 <input type="text" name="url" placeholder="https://…">
 <label>…or upload a file (.epub, .pdf, .txt, .md — epubs become m4b with chapters)</label>
 <input type="file" name="file" accept=".epub,.pdf,.txt,.md">
 <label>Title (optional)</label>
 <input type="text" name="title">
 <label>Backend</label>
 <select name="backend">{backend_options}</select>
 <div id="xai-auth-fields">
  <label>xAI auth</label>
  <select name="xai_auth">
   <option value="oauth">OAuth · subscription</option>
   <option value="api">API key · metered</option>
  </select>
 </div>
 <label>Voice (optional, backend default if blank)</label>
 <input type="text" name="voice" list="voice-options" autocomplete="off">
 <datalist id="voice-options"></datalist>
 <button type="submit">Make episode</button>
 <button type="button" id="clear-form">Clear</button>
 <small>fields are kept after submitting, so you can retry with another
 voice or backend — Clear empties them</small>
</form>
<h2>Jobs</h2>
<ul id="jobs">{jobs}</ul>
<h2>Episodes</h2>
<ul id="episodes">{episodes}</ul>
<h2>Feed</h2>
<p>Subscribe in your podcast app:<br>
<a href="{feed_url}"><code>{feed_url}</code></a></p>
<form method="post" action="/refresh">
 <button type="submit">Refresh feed</button>
 <small>removes episodes whose audio files you've deleted from disk</small>
</form>
<p><img src="{page_qr}" alt="QR code for this page" width="220" height="220"><br>
<small>scan to open this page on another device</small></p>
<script>
(function () {{
  var select = document.querySelector('select[name=backend]');
  var xaiAuthFields = document.getElementById('xai-auth-fields');
  var xaiAuth = document.querySelector('select[name=xai_auth]');
  var voiceInput = document.querySelector('input[name=voice]');
  var voiceList = document.getElementById('voice-options');
  var HINTS = {voice_hints};

  // voice placeholder + suggestions follow the selected backend
  function updateVoiceHints() {{
    var hint = HINTS[select.value];
    if (!hint) {{ voiceInput.placeholder = ''; voiceList.innerHTML = ''; return; }}
    voiceInput.placeholder =
      'default: ' + hint['default'] + ' — e.g. ' +
      hint.voices.slice(0, 3).map(function (v) {{ return v.label; }}).join(', ');
    voiceList.innerHTML = '';
    hint.voices.forEach(function (v) {{
      var option = document.createElement('option');
      option.value = v.id;          // what gets submitted
      option.textContent = v.label; // what the dropdown displays
      voiceList.appendChild(option);
    }});
    xaiAuthFields.style.display = select.value === 'grok' ? '' : 'none';
  }}

  // remember the last-chosen backend on this device
  var saved = localStorage.getItem('textinator-backend');
  if (saved) {{
    for (var i = 0; i < select.options.length; i++) {{
      if (select.options[i].value === saved) {{ select.value = saved; break; }}
    }}
  }}
  var savedXaiAuth = localStorage.getItem('textinator-xai-auth');
  if (savedXaiAuth === 'oauth' || savedXaiAuth === 'api') {{
    xaiAuth.value = savedXaiAuth;
  }} else if (savedXaiAuth) {{
    localStorage.removeItem('textinator-xai-auth');
  }}
  // keep the form contents across submits (the POST redirects back here),
  // so the same text can be re-generated with a different voice/backend
  var fields = ['text', 'url', 'title', 'voice'].map(function (n) {{
    return document.querySelector('[name=' + n + ']');
  }});
  fields.forEach(function (f) {{
    var saved = localStorage.getItem('textinator-field-' + f.name);
    if (saved) f.value = saved;
  }});
  document.querySelector('form[action="/make"]').addEventListener('submit', function () {{
    localStorage.setItem('textinator-backend', select.value);
    localStorage.setItem('textinator-xai-auth', xaiAuth.value);
    fields.forEach(function (f) {{
      localStorage.setItem('textinator-field-' + f.name, f.value);
    }});
  }});
  document.getElementById('clear-form').addEventListener('click', function () {{
    fields.forEach(function (f) {{
      f.value = '';
      localStorage.removeItem('textinator-field-' + f.name);
    }});
    document.querySelector('input[type=file]').value = '';
  }});

  select.addEventListener('change', updateVoiceHints);
  updateVoiceHints();

  // datalist quirk: browsers filter suggestions by the field's current value,
  // so a filled-in voice hides all the other options. Clear on focus so the
  // full list shows; put the old value back if nothing was picked or typed.
  voiceInput.addEventListener('focus', function () {{
    this.dataset.prev = this.value;
    this.dataset.edited = '';
    this.value = '';
  }});
  voiceInput.addEventListener('input', function () {{
    this.dataset.edited = '1';
  }});
  voiceInput.addEventListener('blur', function () {{
    if (!this.value && !this.dataset.edited) {{
      this.value = this.dataset.prev || '';
    }}
  }});

  // live lists: poll so long syntheses show progress and finished episodes
  // appear (with their file links) without reloading
  var jobList = document.getElementById('jobs');
  var episodeList = document.getElementById('episodes');
  function refreshJobs() {{
    fetch('/jobs')
      .then(function (r) {{ return r.json(); }})
      .then(function (jobs) {{
        if (!jobs.length) return;
        jobList.innerHTML = '';
        jobs.forEach(function (j) {{
          var li = document.createElement('li');
          li.className = j.status;
          li.textContent =
            '[' + j.started + '] ' + j.status + ': ' + j.title +
            (j.detail ? ' — ' + j.detail : '');
          jobList.appendChild(li);
        }});
      }})
      .catch(function () {{ /* server briefly away; try again next tick */ }});
    fetch('/episodes')
      .then(function (r) {{ return r.json(); }})
      .then(function (episodes) {{
        if (!episodes.length) return;
        episodeList.innerHTML = '';
        episodes.forEach(function (e) {{
          var li = document.createElement('li');
          var a = document.createElement('a');
          a.href = e.url;
          a.textContent = e.title;
          li.appendChild(a);
          li.appendChild(
            document.createTextNode(' — ' + e.duration + ' · ' + e.date)
          );
          episodeList.appendChild(li);
        }});
      }})
      .catch(function () {{}});
  }}
  setInterval(refreshJobs, 3000);
}})();
</script>
</body></html>"""


def _make_handler(app: _App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        # ---- helpers -----------------------------------------------------
        def _send_html(self, content: str, status: int = 200):
            body = content.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str):
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        # ---- routes ------------------------------------------------------
        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            token = Feed.load(app.feed_dir).token
            if token and path.startswith(f"/{token}/"):
                return self._serve_feed_file(path.removeprefix(f"/{token}/"))
            if path == "/":
                return self._send_html(self._page())
            if path == "/jobs":
                return self._send_jobs()
            if path == "/episodes":
                return self._send_json(_episode_entries(app))
            self.send_error(404)

        def _send_jobs(self):
            from dataclasses import asdict

            with app.lock:
                jobs = [asdict(j) for j in app.jobs]
            self._send_json(jobs)

        def _send_json(self, payload):
            import json

            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/refresh":
                return self._refresh_feed()
            if path != "/make":
                return self.send_error(404)
            length = int(self.headers.get("Content-Length", 0))
            if length > _MAX_UPLOAD_BYTES:
                return self.send_error(413, "upload too large")

            import multipart

            forms, files = multipart.parse_form_data(
                {
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                    "wsgi.input": self.rfile,
                }
            )
            text = (forms.get("text") or "").strip()
            url = (forms.get("url") or "").strip()

            upload_path = None
            upload_name = ""
            part = files.get("file")
            if part is not None and part.filename:
                suffix = Path(part.filename).suffix.lower() or ".txt"
                handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                handle.close()
                part.save_as(handle.name)
                part.close()
                upload_path = Path(handle.name)
                upload_name = part.filename

            if not text and not url and upload_path is None:
                return self._send_html("<p>nothing to read — go back.</p>", 400)
            app.start_job(
                source=url or text,
                backend_name=forms.get("backend") or "edge",
                xai_auth=(forms.get("xai_auth") or "oauth").strip(),
                voice=(forms.get("voice") or "").strip(),
                title=(forms.get("title") or "").strip(),
                upload_path=upload_path,
                upload_name=upload_name,
            )
            self._redirect("/")

        # ---- pieces ------------------------------------------------------
        def _refresh_feed(self):
            """Drop feed entries whose audio the user has deleted from disk."""
            with pipeline._feed_lock:  # don't race a job finishing its save
                feed = Feed.load(app.feed_dir)
                removed = feed.prune_missing()
                if removed:
                    feed.save()
            if removed:
                titles = ", ".join(e.title for e in removed[:5])
                detail = f"removed {len(removed)}: {titles}"
            else:
                detail = "nothing to remove"
            with app.lock:
                app.jobs.insert(
                    0,
                    Job(
                        title=f"feed refresh ({len(feed.episodes)} episodes remain)",
                        status="done",
                        detail=detail,
                    ),
                )
            self._redirect("/")

        def _serve_feed_file(self, relative: str):
            target = (app.feed_dir / relative).resolve()
            if not target.is_file() or app.feed_dir.resolve() not in target.parents:
                return self.send_error(404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                _MIME.get(target.suffix.lower(), "application/octet-stream"),
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _page(self) -> str:
            with app.lock:
                jobs = list(app.jobs)
            job_items = "".join(
                f'<li class="{j.status}">[{j.started}] {j.status}: '
                f"{html.escape(j.title)}"
                f"{' — ' + html.escape(j.detail) if j.detail else ''}</li>"
                for j in jobs
            ) or "<li>none yet</li>"
            options = "".join(
                f'<option value="{b}">{_backend_label(b)}</option>'
                for b in AVAILABLE_BACKENDS
            )
            episode_items = "".join(
                f'<li><a href="{html.escape(e["url"], quote=True)}">'
                f'{html.escape(e["title"])}</a>'
                f' — {e["duration"]} · {e["date"]}</li>'
                for e in _episode_entries(app)
            ) or "<li>none yet</li>"
            import json

            return _PAGE.format(
                backend_options=options,
                jobs=job_items,
                episodes=episode_items,
                feed_url=f"{app.base_url}/feed.xml",
                page_qr=app.page_qr,
                voice_hints=json.dumps(app.voice_hints),
            )

    return Handler


def run_server(
    feed_dir: Path,
    port: int = 8765,
    host: str = "0.0.0.0",
    url_host: str | None = None,
) -> None:
    """Start the web UI + private feed server. Runs until interrupted."""
    feed = Feed.load(feed_dir)
    token = feed.ensure_token()
    ui_host = url_host or lan_ip()
    base_url = f"http://{ui_host}:{port}/{token}"
    if feed.base_url != base_url:
        feed.base_url = base_url
        if feed.episodes:
            feed.save()  # rewrite enclosure URLs to match how we serve
        else:
            feed_dir.mkdir(parents=True, exist_ok=True)
            feed.save()

    app = _App(
        feed_dir=feed_dir,
        base_url=base_url,
        page_url=f"http://{ui_host}:{port}/",
        # pick up cloned grok voices (by name) for the dropdown; new clones
        # appear after a server restart
        voice_hints=build_voice_hints(include_grok_custom=True),
    )
    server = ThreadingHTTPServer((host, port), _make_handler(app))
    print(f"textinator web UI: http://{ui_host}:{port}/")
    print(f"private podcast feed: {base_url}/feed.xml")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
