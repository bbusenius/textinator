from xml.etree import ElementTree as ET

from textinator.deliver import lan_ip, sync
from textinator.feed import Feed


def _feed_dir_with_episode(tmp_path, make_mp3):
    feed = Feed.load(tmp_path / "feed")
    feed.base_url = "https://old.example/f/X"
    feed.episodes_dir.mkdir(parents=True)
    mp3 = make_mp3("ep.mp3")
    target = feed.episodes_dir / mp3.name
    mp3.rename(target)
    feed.add_episode(target, title="Ep", duration_seconds=1)
    feed.save()
    return feed.feed_dir


def test_lan_ip_returns_something():
    ip = lan_ip()
    assert ip.count(".") == 3


# realistic `ip -4 -o addr show up` output: VPN owns the default route,
# docker/bridge interfaces present, real LAN is wifi
_IP_OUTPUT = """\
1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: wlan0    inet 192.168.50.42/24 brd 192.168.50.255 scope global dynamic noprefixroute wlan0\\       valid_lft 5000sec preferred_lft 5000sec
7: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever
9: br-example    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-example\\       valid_lft forever preferred_lft forever
188: tun0    inet 10.8.0.2/24 brd 10.8.0.255 scope global noprefixroute tun0\\       valid_lft forever preferred_lft forever
"""


def test_parse_ip_addr():
    from textinator.deliver import _parse_ip_addr

    pairs = _parse_ip_addr(_IP_OUTPUT)
    assert ("wlan0", "192.168.50.42") in pairs
    assert ("tun0", "10.8.0.2") in pairs
    # loopback is scope host, not global
    assert not any(iface == "lo" for iface, _ip in pairs)


def test_lan_ip_skips_vpn_and_docker(monkeypatch):
    import subprocess as sp

    from textinator import deliver

    monkeypatch.setattr(
        sp, "run",
        lambda *a, **k: sp.CompletedProcess(a, 0, stdout=_IP_OUTPUT, stderr=""),
    )
    # wifi wins over the VPN (tun0) and the docker/bridge interfaces
    assert deliver.lan_ip() == "192.168.50.42"


def test_address_rank_prefers_home_lan():
    from textinator.deliver import _address_rank

    assert _address_rank("192.168.50.42") < _address_rank("10.8.0.2")
    assert _address_rank("10.8.0.2") < _address_rank("172.18.0.1")
    assert _address_rank("172.18.0.1") < _address_rank("8.8.8.8")


def test_sync_local_copies_everything(tmp_path, make_mp3):
    feed_dir = _feed_dir_with_episode(tmp_path, make_mp3)
    dest = tmp_path / "synced"
    sync(feed_dir, str(dest))
    assert (dest / "feed.xml").is_file()
    assert (dest / "feed.json").is_file()
    assert (dest / "episodes" / "ep.mp3").is_file()


def test_sync_rewrites_base_url(tmp_path, make_mp3):
    feed_dir = _feed_dir_with_episode(tmp_path, make_mp3)
    dest = tmp_path / "synced"
    sync(feed_dir, str(dest), base_url="https://new.example/f/Y")
    url = (
        ET.parse(dest / "feed.xml")
        .getroot()
        .find("channel/item/enclosure")
        .get("url")
    )
    assert url == "https://new.example/f/Y/episodes/ep.mp3"


def test_token_persisted(tmp_path):
    feed = Feed.load(tmp_path / "feed")
    token = feed.ensure_token()
    assert len(token) >= 16
    feed.save()
    assert Feed.load(tmp_path / "feed").token == token
