"""Shared pytest setup.

- Puts the project root on sys.path so tests import the stdlib backend as
  ``server.library`` without an editable install.
- Registers a ``--real-devices`` flag and a ``real_devices`` marker. Tests that
  talk to a physical USB instrument carry ``@pytest.mark.real_devices`` and are
  skipped by default; run the suite with ``--real-devices`` to enable them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_addoption(parser):
    parser.addoption(
        "--real-devices",
        action="store_true",
        default=False,
        help="run tests that talk to physical USB instruments (off by default)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_devices: requires a physical instrument attached; "
        "skipped unless --real-devices is passed",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--real-devices"):
        return
    skip = pytest.mark.skip(reason="needs --real-devices to touch physical hardware")
    for item in items:
        if "real_devices" in item.keywords:
            item.add_marker(skip)
