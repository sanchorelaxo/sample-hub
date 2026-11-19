#!/usr/bin/env python3
"""One-shot hardware probe for sample-hub status module.

Walks sysfs for USB topology, maps ttyACM devices to USB parents, and lists
block devices + mounts so the status probe's detection logic is built on real
ground truth, not guesses.
"""
import os
import sys
from pathlib import Path

USB = Path("/sys/bus/usb/devices")
TTY = Path("/sys/class/tty")
BLOCK = Path("/sys/block")

def read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def is_hub(dev):
    cls = read(dev / "bDeviceClass")
    return cls == "09" or cls == "0x09"


def tty_children(devpath):
    out = []
    for t in sorted(TTY.glob("ttyACM*")):
        d = (t / "device").resolve()
        if str(d).endswith(devpath) or devpath in str(d):
            out.append(t.name)
    return out


print("=== USB topology (busnum/devnum path vendor:product product mfr [tty]) ===")
rows = []
for dev in sorted(USB.glob("*")):
    name = dev.name
    if ":" not in name and not name.endswith("usb1") and "-" not in name:
        continue
    vid = read(dev / "idVendor")
    pid = read(dev / "idProduct")
    prod = read(dev / "product")
    mfr = read(dev / "manufacturer")
    busnum = read(dev / "busnum")
    devnum = read(dev / "devnum")
    cls = read(dev / "bDeviceClass")
    parent = read(dev / ".." / "devpath") or "?"
    tty = ",".join(tty_children(name))
    hub = "HUB" if is_hub(dev) else ""
    rows.append((busnum, devnum, name, f"{vid}:{pid}", cls, hub, mfr, prod, parent, tty))

for r in sorted(rows, key=lambda x: (x[0], x[1])):
    bus, dn, name, vp, cls, hub, mfr, prod, parent, tty = r
    t = f" tty=[{tty}]" if tty else ""
    h = f" {hub}" if hub else ""
    print(f"bus{bus} dev{dn} {name:10s} {vp:9s} cls={cls}{h} mfr={mfr!r} prod={prod!r} parent={parent}{t}")

print()
print("=== ttyACM -> USB parent resolution ===")
for t in sorted(TTY.glob("ttyACM*")):
    dev = (t / "device").resolve()
    # walk up to find the USB interface's parent device
    p = dev
    vid = pid = prod = ""
    while str(p) != "/":
        if (p / "idVendor").exists():
            vid = read(p / "idVendor")
            pid = read(p / "idProduct")
            prod = read(p / "product")
            break
        p = p.parent
    print(f"{t.name} -> {vid}:{pid} {prod!r}")

print()
print("=== serial-by-id (stable names) ===")
for d in sorted(Path("/dev/serial/by-id").glob("*")) if Path("/dev/serial/by-id").exists() else []:
    print(f"{d.name} -> {os.path.realpath(d)}")

print()
print("=== block devices (disk mode candidates) ===")
for b in sorted(BLOCK.glob("sd*")):
    vendor = read(b / "device" / "vendor")
    model = read(b / "device" / "model")
    removable = read(b / "removable")
    print(f"/dev/{b.name} removable={removable} vendor={vendor!r} model={model!r}")

print()
print("=== mounts (vfat/exfat/op1/sp404) ===")
try:
    mounts = Path("/proc/mounts").read_text()
    for line in mounts.splitlines():
        if any(k in line.lower() for k in ("vfat", "exfat", "fuseblk", "msdos")):
            print(line)
except OSError as e:
    print("no /proc/mounts", e)

print()
print("=== lsusb (raw) ===")
import subprocess
try:
    print(subprocess.run(["lsusb"], capture_output=True, text=True).stdout)
except FileNotFoundError:
    print("lsusb not found")

print("=== done ===")
