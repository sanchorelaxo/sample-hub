"""Regression tests for the live roots resolver in server.__main__."""

from server.status import DeviceStatus, GREEN


def _device(device_id: str, detail: str) -> DeviceStatus:
    return DeviceStatus(
        id=device_id,
        label=device_id,
        transport="usb",
        state=GREEN,
        present=True,
        detail=detail,
    )


def test_live_roots_accepts_target_positionally(monkeypatch):
    """make_server calls its roots callable as ``target -> roots``.

    ``_live_roots`` must accept that positional target without letting it
    clobber the probe. Regression: the live server's source endpoints
    (/api/devices/<id>/source) returned net::ERR_EMPTY_RESPONSE because
    ``_live_roots`` bound the target string to its ``probe_fn`` parameter.
    """
    import server.__main__ as main

    def fake_probe(mounter=None):
        return [
            _device("op1", "/mnt/op1"),
            _device("sp404", "/mnt/SP404MKII \u00b7 manual IMPORT on unit"),
        ]

    monkeypatch.setattr(main, "probe", fake_probe)

    roots = main._live_roots("op1")

    assert roots == {
        "op1_mount": "/mnt/op1",
        "sp404_card_root": "/mnt/SP404MKII",
    }
