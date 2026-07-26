"""Delivery helpers: serve the feed directory over HTTP, or sync it elsewhere.

These are conveniences around the core contract — the feed directory is
self-contained and can be hosted by anything that serves static files.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .feed import Feed


# interfaces that other LAN devices can't reach: VPNs, containers, bridges
_VIRTUAL_PREFIXES = (
    "lo", "tun", "tap", "wg", "docker", "br-", "veth", "virbr",
    "vmnet", "zt", "tailscale",
)


def _address_rank(ip: str) -> int:
    """Prefer home-LAN-looking addresses when several interfaces qualify."""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    octets = ip.split(".")
    if octets[0] == "172" and 16 <= int(octets[1]) <= 31:
        return 2
    return 3


def _parse_ip_addr(output: str) -> list[tuple[str, str]]:
    """Parse `ip -4 -o addr show up` into (interface, address) pairs."""
    pairs = []
    for line in output.splitlines():
        fields = line.split()
        # "2: wlan0 inet 192.168.50.42/24 brd ... scope global dynamic ..."
        if len(fields) >= 4 and fields[2] == "inet" and "global" in fields:
            pairs.append((fields[1], fields[3].split("/")[0]))
    return pairs


def lan_ip() -> str:
    """The address other devices on the LAN should use to reach this machine.

    The naive trick (UDP-connect and read the local address) returns whatever
    interface owns the default route — with a VPN up, that's the tunnel, and
    the printed URLs/QR/feed become unreachable from the LAN. So: enumerate
    real interfaces, skip virtual ones, prefer private home-LAN ranges.
    """
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "up"],
            capture_output=True, text=True, timeout=5,
        )
        candidates = [
            (iface, ip)
            for iface, ip in _parse_ip_addr(result.stdout)
            if not iface.startswith(_VIRTUAL_PREFIXES)
            and _address_rank(ip) < 3  # private ranges only
        ]
        if candidates:
            return min(candidates, key=lambda c: _address_rank(c[1]))[1]
    except (OSError, subprocess.SubprocessError):
        pass
    # fallback: default-route interface (better than nothing)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # TEST-NET, never routed
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console readable
        pass


def serve(
    feed_dir: Path,
    port: int = 8080,
    host: str = "0.0.0.0",
    url_host: str | None = None,
) -> None:
    """Serve the feed directory on the LAN, rewriting feed.xml to match.

    Runs until interrupted. Intended for home/LAN use — there is no auth
    beyond the feed's URL token, and no TLS; put real deployments behind
    object storage or a proper web server.
    """
    feed = Feed.load(feed_dir)
    if not feed.episodes:
        raise FileNotFoundError(
            f"{feed_dir} has no episodes yet — run textinator on some text first"
        )
    base_url = f"http://{url_host or lan_ip()}:{port}"
    if feed.base_url != base_url:
        feed.base_url = base_url
        feed.save()  # enclosure URLs must match how we're serving

    handler = partial(_QuietHandler, directory=str(feed_dir))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Serving feed directory {feed_dir}")
    print(f"Subscribe in your podcast app: {base_url}/feed.xml")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def sync(feed_dir: Path, dest: str, base_url: str | None = None) -> None:
    """Copy the feed directory to ``dest`` (local path or rsync target).

    ``dest`` with a colon (host:path) goes via rsync; local paths use rsync
    when available, else a plain copy. Pass ``base_url`` to rewrite feed.xml
    for the URL the destination will be served from.
    """
    feed = Feed.load(feed_dir)
    if base_url and feed.base_url != base_url:
        feed.base_url = base_url
        feed.save()

    remote = ":" in dest
    rsync = shutil.which("rsync")
    source = str(feed_dir).rstrip("/") + "/"  # trailing slash: sync contents
    if rsync:
        result = subprocess.run([rsync, "-a", "--delete", source, dest])
        if result.returncode != 0:
            raise RuntimeError(f"rsync exited with {result.returncode}")
    elif remote:
        raise RuntimeError("rsync is required for remote destinations")
    else:
        shutil.copytree(feed_dir, dest, dirs_exist_ok=True)
    print(f"synced {feed_dir} -> {dest}")
