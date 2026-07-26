"""Private podcast feed output adapter.

Emits a self-contained *feed directory*:

    feed-dir/
      feed.xml        <- valid podcast RSS, URLs built from base_url
      feed.json       <- manifest (source of truth; feed.xml is regenerated)
      episodes/*.mp3  <- the audio enclosures

The generator does NOT host. Serving the directory (LAN static server, object
storage, VPS) is a deploy-time choice. Privacy comes from an unguessable token
in the base URL path — standard private-podcast pattern.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_MIME_BY_SUFFIX = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "m4b": "audio/mp4",
}


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


@dataclass
class Episode:
    guid: str
    title: str
    filename: str  # relative to episodes/
    pub_date: str  # ISO 8601, UTC
    duration_seconds: float
    size_bytes: int
    description: str = ""

    @property
    def mime_type(self) -> str:
        suffix = self.filename.rsplit(".", 1)[-1].lower()
        return _MIME_BY_SUFFIX.get(suffix, "audio/mpeg")


@dataclass
class Feed:
    feed_dir: Path
    title: str = "Textinator"
    description: str = "Anything I'd rather listen to than read."
    base_url: str = ""
    language: str = "en"
    author: str = "Textinator"
    #: unguessable path token for private serving (generated when first needed)
    token: str = ""
    episodes: list[Episode] = field(default_factory=list)

    MANIFEST = "feed.json"
    XML = "feed.xml"
    EPISODES_DIR = "episodes"

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, feed_dir: Path) -> "Feed":
        """Load an existing feed directory, or return a fresh Feed for it."""
        feed_dir = Path(feed_dir)
        manifest = feed_dir / cls.MANIFEST
        if not manifest.is_file():
            return cls(feed_dir=feed_dir)
        data = json.loads(manifest.read_text())
        episodes = [Episode(**e) for e in data.pop("episodes", [])]
        return cls(feed_dir=feed_dir, episodes=episodes, **data)

    def save(self) -> Path:
        """Write feed.json and regenerate feed.xml. Returns the feed.xml path."""
        self.feed_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "title": self.title,
            "description": self.description,
            "base_url": self.base_url,
            "language": self.language,
            "author": self.author,
            "token": self.token,
            "episodes": [asdict(e) for e in self.episodes],
        }
        (self.feed_dir / self.MANIFEST).write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        xml_path = self.feed_dir / self.XML
        xml_path.write_bytes(self.to_xml())
        return xml_path

    def ensure_token(self) -> str:
        """Return the privacy token, generating and persisting one if absent."""
        if not self.token:
            self.token = secrets.token_urlsafe(16)
        return self.token

    # -- episodes ------------------------------------------------------------

    @property
    def episodes_dir(self) -> Path:
        return self.feed_dir / self.EPISODES_DIR

    def add_episode(
        self,
        audio_path: Path,
        title: str,
        duration_seconds: float,
        description: str = "",
    ) -> Episode:
        """Register an audio file already placed in episodes/ as a new episode."""
        audio_path = Path(audio_path)
        episode = Episode(
            guid=str(uuid.uuid4()),
            title=title,
            filename=audio_path.name,
            pub_date=datetime.now(timezone.utc).isoformat(),
            duration_seconds=duration_seconds,
            size_bytes=audio_path.stat().st_size,
            description=description,
        )
        self.episodes.append(episode)
        return episode

    def prune_missing(self) -> list[Episode]:
        """Drop episodes whose audio file no longer exists on disk.

        Lets the user reclaim space by deleting listened-to files from
        episodes/ — the next prune removes them from the feed too. Returns
        the episodes that were removed.
        """
        removed = [
            e for e in self.episodes
            if not (self.episodes_dir / e.filename).is_file()
        ]
        if removed:
            self.episodes = [e for e in self.episodes if e not in removed]
        return removed

    def enclosure_url(self, episode: Episode) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/{self.EPISODES_DIR}/{quote(episode.filename)}"

    # -- RSS -----------------------------------------------------------------

    def to_xml(self) -> bytes:
        ET.register_namespace("itunes", ITUNES_NS)
        rss = ET.Element("rss", attrib={"version": "2.0"})
        channel = ET.SubElement(rss, "channel")

        def sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
            el = ET.SubElement(parent, tag)
            el.text = text
            return el

        sub(channel, "title", self.title)
        sub(channel, "link", self.base_url or "http://localhost/")
        sub(channel, "description", self.description)
        sub(channel, "language", self.language)
        sub(channel, f"{{{ITUNES_NS}}}author", self.author)
        block = ET.SubElement(channel, f"{{{ITUNES_NS}}}block")
        block.text = "yes"  # ask indexers not to list this private feed
        if self.episodes:
            latest = max(
                datetime.fromisoformat(e.pub_date) for e in self.episodes
            )
            sub(channel, "lastBuildDate", format_datetime(latest))

        # Newest first, like podcast apps expect.
        for episode in sorted(self.episodes, key=lambda e: e.pub_date, reverse=True):
            item = ET.SubElement(channel, "item")
            sub(item, "title", episode.title)
            if episode.description:
                sub(item, "description", episode.description)
            guid = sub(item, "guid", episode.guid)
            guid.set("isPermaLink", "false")
            sub(
                item,
                "pubDate",
                format_datetime(datetime.fromisoformat(episode.pub_date)),
            )
            ET.SubElement(
                item,
                "enclosure",
                attrib={
                    "url": self.enclosure_url(episode),
                    "length": str(episode.size_bytes),
                    "type": episode.mime_type,
                },
            )
            sub(
                item,
                f"{{{ITUNES_NS}}}duration",
                _format_duration(episode.duration_seconds),
            )

        ET.indent(rss)
        return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
