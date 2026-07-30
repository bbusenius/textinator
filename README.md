# Textinator

Turn *any* text into an audiobook you can listen to in your podcast app.
Paste text (or point at a file), and it becomes an episode in a private
podcast feed. Read-it-later, but for your ears.

## Setup

Requires Python 3.12+ and `ffmpeg`/`ffprobe` on PATH.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# Open the web interface to convert text
textinator web

# a text/markdown file -> episode (edge-tts, free, default backend)
textinator note.txt

# a web article, an epub (m4b with chapter markers), a PDF
textinator https://example.com/article
textinator book.epub
textinator paper.pdf

# pipe/paste from stdin
xclip -o | textinator -

# pick a voice
textinator note.txt --voice en-US-AriaNeural
textinator --list-voices en          # see what's available
textinator --backend grok --list-voices  # live xAI built-in + custom catalog

# choose where the feed lives and the URL it will be served at
textinator note.txt --feed-dir ~/feeds/textinator \
    --base-url https://myhost.example/feeds/SOME-RANDOM-TOKEN

# see what would be sent before using a guarded backend
textinator big-article.txt --backend grok --dry-run

# connect xAI subscription OAuth (stored only by Textinator)
textinator auth xai
textinator auth status

# explicitly choose subscription OAuth or the metered API key
textinator article.txt --backend grok --xai-auth oauth
textinator article.txt --backend grok --xai-auth api
```

The `--base-url` is stored in the feed after the first run; you only pass it
again to change it.

## The feed directory

Each run appends one episode to a self-contained feed directory:

```
feed/
  feed.xml        <- podcast RSS (URLs built from your base-url)
  feed.json       <- manifest (source of truth)
  episodes/*.mp3  <- the audio
```

Textinator **generates** the feed; it does not host it. Serve the directory
any way you like — the URLs in `feed.xml` just have to match `--base-url`.

## Subscribing in your podcast app

The zero-setup way (home LAN):

```bash
textinator web            # or: textinator serve  (no UI, just the feed)
```

`textinator web` starts a small server that does two things:
- a paste form at `http://<lan-ip>:8765/` — open it on your phone, paste
  text, a URL (articles, or direct links to `.pdf`/`.epub`), or upload an
  epub/PDF/text file, then tap **Make episode**;
- your private feed at `http://<lan-ip>:8765/<token>/feed.xml` (the token is
  generated once and stored in `feed.json`; the exact URL is printed at
  startup).

In your podcast app choose **Add show by URL** and enter that feed URL. New
episodes appear whenever you make one and the app refreshes.

For listening away from home, host the directory anywhere static files live:

```bash
# copy/rsync the feed dir (rewrites feed.xml for its public URL first)
textinator sync myhost:/var/www/f/<token> --base-url https://myhost.example/f/<token>
```

Object storage (R2/B2/S3), any static host, or a reverse-proxied box all
work — the feed directory is self-contained.

The feed sets `itunes:block=yes` so directories that respect it won't index
it; the random token is what actually keeps it private. Don't share the URL.
Prefer HTTPS hosting for anything beyond your LAN.

## Backends

| backend  | cost      | needs network | notes                                  |
|----------|-----------|---------------|----------------------------------------|
| `edge`   | free      | yes           | default; Azure neural voices, unofficial |
| `kokoro` | free      | no (local)    | offline CPU TTS; `pip install -e '.[kokoro]'` (~330MB model auto-downloads on first use); voices like `af_heart`, `am_adam`, `bf_emma` |
| `grok`   | subscription / metered | yes | xAI OAuth or `XAI_API_KEY`; built-in and custom voices discovered live |

Guarded backends refuse runs over `--max-chars` (default 50,000) unless you
pass `--force`. `--dry-run` shows the character count and a short cost label:
`subscription`, `metered`, or `free`. Textinator does not invent a dollar
estimate from a hardcoded rate.

OAuth is the default. Select metered API-key authentication explicitly with
`--xai-auth api`. If OAuth fails, Textinator does not silently start metered API
usage; the CLI asks first, or you can explicitly pass `--api-fallback`. OAuth
tokens live in Textinator's private data directory and are not shared with or
read from other applications.

The web UI checks Grok availability for the selected auth mode and loads its
current voice catalog from xAI. If credentials or connectivity are unavailable,
Grok is shown as unavailable and cannot be submitted; Edge and Kokoro continue
to work normally. Textinator does not substitute a stale Grok voice list.

## Development

```bash
pip install -e '.[dev]'
pytest -q     # no network, no paid calls — backends are mocked
```
