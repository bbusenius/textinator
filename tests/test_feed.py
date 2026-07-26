from xml.etree import ElementTree as ET

from textinator.feed import ITUNES_NS, Feed, _format_duration


def _feed_with_episode(tmp_path, make_mp3, title="Test Episode"):
    feed = Feed.load(tmp_path / "feed")
    feed.base_url = "https://host.example/f/SECRET-TOKEN"
    feed.episodes_dir.mkdir(parents=True)
    mp3 = make_mp3("ep one.mp3")
    target = feed.episodes_dir / mp3.name
    mp3.rename(target)
    feed.add_episode(target, title=title, duration_seconds=61.4)
    feed.save()
    return feed


def test_format_duration():
    assert _format_duration(61.4) == "1:01"
    assert _format_duration(3661) == "1:01:01"
    assert _format_duration(5) == "0:05"


def test_feed_xml_is_valid_rss(tmp_path, make_mp3):
    feed = _feed_with_episode(tmp_path, make_mp3)
    root = ET.parse(feed.feed_dir / "feed.xml").getroot()
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    for tag in ("title", "link", "description", "language"):
        assert channel.find(tag) is not None, f"missing <{tag}>"
    assert channel.find(f"{{{ITUNES_NS}}}author") is not None


def test_item_enclosure_and_metadata(tmp_path, make_mp3):
    feed = _feed_with_episode(tmp_path, make_mp3)
    item = ET.parse(feed.feed_dir / "feed.xml").getroot().find("channel/item")
    assert item.find("title").text == "Test Episode"
    guid = item.find("guid")
    assert guid.get("isPermaLink") == "false"
    assert item.find("pubDate") is not None
    assert item.find(f"{{{ITUNES_NS}}}duration").text == "1:01"

    enclosure = item.find("enclosure")
    url = enclosure.get("url")
    # URL from base_url + episodes dir, filename percent-encoded
    assert url == "https://host.example/f/SECRET-TOKEN/episodes/ep%20one.mp3"
    assert enclosure.get("type") == "audio/mpeg"
    size = (feed.episodes_dir / "ep one.mp3").stat().st_size
    assert enclosure.get("length") == str(size)
    assert size > 0


def test_prune_missing_removes_deleted_audio(tmp_path, make_mp3):
    feed = _feed_with_episode(tmp_path, make_mp3, title="Keep Me")
    mp3 = make_mp3("gone.mp3")
    target = feed.episodes_dir / mp3.name
    mp3.rename(target)
    feed.add_episode(target, title="Delete Me", duration_seconds=3)
    feed.save()

    target.unlink()  # user listened and deleted the file
    reloaded = Feed.load(tmp_path / "feed")
    removed = reloaded.prune_missing()
    assert [e.title for e in removed] == ["Delete Me"]
    assert [e.title for e in reloaded.episodes] == ["Keep Me"]
    reloaded.save()

    items = ET.parse(reloaded.feed_dir / "feed.xml").getroot().findall("channel/item")
    assert [i.find("title").text for i in items] == ["Keep Me"]


def test_prune_missing_noop_when_all_present(tmp_path, make_mp3):
    feed = _feed_with_episode(tmp_path, make_mp3)
    assert feed.prune_missing() == []
    assert len(feed.episodes) == 1


def test_roundtrip_load_appends(tmp_path, make_mp3):
    _feed_with_episode(tmp_path, make_mp3, title="First")
    feed = Feed.load(tmp_path / "feed")
    assert feed.base_url == "https://host.example/f/SECRET-TOKEN"
    assert [e.title for e in feed.episodes] == ["First"]

    mp3 = make_mp3("two.mp3")
    target = feed.episodes_dir / mp3.name
    mp3.rename(target)
    feed.add_episode(target, title="Second", duration_seconds=5)
    feed.save()

    reloaded = Feed.load(tmp_path / "feed")
    assert [e.title for e in reloaded.episodes] == ["First", "Second"]
    items = ET.parse(feed.feed_dir / "feed.xml").getroot().findall("channel/item")
    assert len(items) == 2
    # newest first in the XML
    assert items[0].find("title").text == "Second"
