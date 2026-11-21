"""Sample Hub launcher: ``python -m server`` serves the rack client + JSON API.

Loopback only, stdlib only. On every status poll it auto-mounts a visible-but-
unmounted OP-1 / SD-card / RPI-RP2 volume through udisksctl (no sudo), and it
resolves each instrument's transfer root from that same live probe, so a drop
lands on the device that is actually present now.

    python -m server --port 8766

Set SAMPLE_HUB_DATA to move the library/staging root (default ``~/.sample-hub``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from server import hub_server
from server.status import GREEN, _udisksctl_mount, default_staging_dir, probe

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"


def _live_roots(target: str | None = None, probe_fn=None) -> dict:
    """Transfer roots read from one live status pass.

    ``target`` is accepted and ignored: ``hub_server.make_server`` calls its
    ``roots`` callable as ``target -> roots``, and the live probe enumerates
    every present device and returns all their roots at once rather than
    resolving a single target. ``probe_fn`` defaults to the live ``probe`` but
    is injectable for tests.

    Only the two mount-backed targets need a root: the OP-1 mount and the
    SP-404 card root. Spotykach goes over USB CDC (no card root), and Wave
    Bard / FX Wizard draft to the shared staging dir. An instrument that is not
    present right now yields no root; a drop on its red pane is refused by the
    client before it would ever reach a writer.
    """
    if probe_fn is None:
        probe_fn = probe
    devices = {device.id: device for device in probe_fn(mounter=_udisksctl_mount)}
    roots: dict = {}
    op1 = devices.get("op1")
    if op1 is not None and op1.state == GREEN:
        roots["op1_mount"] = op1.detail
    sp404 = devices.get("sp404")
    if sp404 is not None and sp404.state == GREEN:
        # detail is "<mount> · manual IMPORT on unit" for a green SP-404 card.
        roots["sp404_card_root"] = sp404.detail.split(" · ")[0]
    return roots


def build_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    probe_kwargs: dict | None = None,
    roots=None,
):
    """Build (without serving) a hub server wired to the live hardware.

    ``probe_kwargs`` and ``roots`` exist so tests can inject an empty sysfs
    tree and fixed roots; by default the status endpoint auto-mounts and each
    convert resolves its roots from a fresh live probe.
    """
    kwargs = dict(probe_kwargs or {})
    kwargs.setdefault("mounter", _udisksctl_mount)
    if roots is None:
        roots = _live_roots
    return hub_server.make_server(
        host,
        port,
        static_dir=CLIENT_DIR,
        probe_kwargs=kwargs,
        roots=roots,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sample-hub",
        description="Serve the rack client + JSON API on the loopback.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SAMPLE_HUB_PORT", DEFAULT_PORT)),
    )
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port)
    host, port = server.server_address[:2]
    print(f"sample-hub {hub_server.VERSION} on http://{host}:{port}", file=sys.stderr)
    print(f"  client: {CLIENT_DIR}", file=sys.stderr)
    print(f"  data:   {default_staging_dir().parent}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
