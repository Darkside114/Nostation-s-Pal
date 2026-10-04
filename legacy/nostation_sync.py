#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOSTATION hub clock sync.

Replicates exactly what the "Sync / 同步" button on https://config.matrix-lab.com/
(the Matrix Lab Vial build, AuxDisplay tab) does:

    report[0..] = FD 37 <year_hi> <year_lo> month day weekday hour minute second

sent as a 32-byte Vial raw-HID report (report id 0) to the interface with
usage_page 0xFF60 / usage 0x61, padded with zeros. The device answers with
FD 37 AA on success. This is AMK_PROTOCOL_PREFIX / AMK_PROTOCOL_SET_DATETIME
from the Matrix Lab Vial source.

Usage:
    python nostation_sync.py [--wait SECONDS] [--verbose] [--all] [--pcb PID]
"""

import argparse
import datetime
import os
import sys
import time

try:
    import hid
except Exception as exc:  # pragma: no cover - reported to the log file
    print("cannot import hidapi: {}".format(exc), file=sys.stderr)
    raise

# ---------------------------------------------------------------- protocol ---

MSG_LEN = 32
VIA_RAW_USAGE_PAGE = 0xFF60
VIA_RAW_USAGE = 0x61

AMK_PREFIX = 0xFD
AMK_OK = 0xAA
AMK_SET_DATETIME = 0x37

WEEKDAY_NAMES = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Matrix Lab (AMK) devices seen on this machine. The hub is the default target.
NOSTATION_PID = 0x5748
MATRIX_LAB_VID = 0x4D58
KNOWN_PIDS = {
    0x5748: "NOSTATION",
    0x0103: "100NG Edition",
    0x1510: "Matrix Lab 1510",
    0x1587: "Matrix Lab 1587",
    0x3858: "Matrix Lab 3858",
    0x4643: "Matrix Lab 4643",
    0x4D54: "Matrix Lab 4D54",
    0x4E80: "Matrix Lab 4E80",
    0xB17F: "Matrix Lab B17F",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = None


def data_dir():
    """Where logs live: <script dir>\\logs, or NOSTATION_SYNC_DIR if set.

    Falls back to the script directory when no logs folder can be created, so
    logging never crashes a sync.
    """
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR
    candidates = []
    override = os.environ.get("NOSTATION_SYNC_DIR")
    if override:
        candidates.append(override)
    candidates.append(os.path.join(SCRIPT_DIR, "logs"))
    for cand in candidates:
        try:
            os.makedirs(cand, exist_ok=True)
            _DATA_DIR = cand
            return cand
        except Exception:
            continue
    _DATA_DIR = SCRIPT_DIR
    return _DATA_DIR


LOG_MAX_BYTES = 128 * 1024


def _trim_and_append(path, line):
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-400:]
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def log(msg, verbose=False):
    line = "{}  {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if verbose:
        try:
            print(line)
        except Exception:
            pass
    _trim_and_append(os.path.join(data_dir(), "nostation-sync.log"), line)


# ----------------------------------------------------------------- devices ---

def vial_raw_hid_devices():
    out = []
    for d in hid.enumerate():
        if d.get("usage_page") == VIA_RAW_USAGE_PAGE and d.get("usage") == VIA_RAW_USAGE:
            out.append(d)
    return out


def describe(d):
    return "{:04x}:{:04x} {} (iface {})".format(
        d["vendor_id"], d["product_id"],
        (d.get("product_string") or KNOWN_PIDS.get(d["product_id"], "?")),
        d.get("interface_number"))


def pick_targets(all_devices=False, pcb=None):
    """Return the raw-HID interfaces to sync.

    Default: the NOSTATION hub only (was a deliberate choice: syncing the
    hub's clock is the goal, other AMK keyboards are left alone). Use --all to
    sync every Matrix Lab raw-HID device, or --pcb to name one product id.
    """
    found = vial_raw_hid_devices()
    if pcb is not None:
        return [d for d in found if d["product_id"] == pcb]
    if all_devices:
        return [d for d in found if d["vendor_id"] == MATRIX_LAB_VID]
    hubs = [d for d in found if d["product_id"] == NOSTATION_PID]
    if hubs:
        return hubs
    # Fall back to any Matrix Lab raw-HID device so a renamed/updated hub
    # firmware still gets its clock set.
    return [d for d in found if d["vendor_id"] == MATRIX_LAB_VID]


# ------------------------------------------------------------------ syncing ---

def drain(dev, timeout_ms=40):
    got = []
    while True:
        r = dev.read(MSG_LEN, timeout_ms=timeout_ms)
        if not r:
            return got
        got.append(bytes(r))


def set_datetime(dev, when, timeout_ms=2000):
    """Send AMK SET_DATETIME. Returns (ok, detail)."""
    payload = bytes([
        AMK_PREFIX, AMK_SET_DATETIME,
        (when.year >> 8) & 0xFF, when.year & 0xFF,
        when.month, when.day, when.isoweekday(),
        when.hour, when.minute, when.second,
    ])
    payload += b"\x00" * (MSG_LEN - len(payload))

    drain(dev)
    dev.write(b"\x00" + payload)

    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=150)
        if not r:
            continue
        resp = bytes(r)
        if len(resp) > 2 and resp[0] == AMK_PREFIX and resp[1] == AMK_SET_DATETIME:
            if resp[2] == AMK_OK:
                return True, "ack"
            return False, "device replied 0x{:02x}".format(resp[2])
    return False, "no response within {} ms".format(timeout_ms)


def sync_once(targets, when, verbose=False):
    results = []
    for d in targets:
        label = describe(d)
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as exc:
            log("  {} -> open failed: {}".format(label, exc), verbose)
            results.append(False)
            continue
        try:
            ok, detail = set_datetime(dev, when)
        except Exception as exc:
            ok, detail = False, "error: {}".format(exc)
        finally:
            try:
                dev.close()
            except Exception:
                pass
        log("  {} -> {} ({})".format(label, "synced" if ok else "FAILED", detail), verbose)
        results.append(ok)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync the Matrix Lab NOSTATION hub clock")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="seconds to keep looking for the device before giving up")
    parser.add_argument("--verbose", action="store_true", help="also print to stdout")
    parser.add_argument("--all", action="store_true",
                        help="sync every Matrix Lab raw-HID device, not just the hub")
    parser.add_argument("--pcb", type=lambda s: int(s, 0), default=None,
                        help="only sync this product id, e.g. --pcb 0x5748")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the targets and the payload without sending anything")
    args = parser.parse_args(argv)

    started = time.time()
    log("run start (wait={}s, all={}, pcb={})".format(args.wait, args.all, args.pcb), args.verbose)

    targets = []
    while True:
        try:
            targets = pick_targets(args.all, args.pcb)
        except Exception as exc:
            log("enumeration failed: {}".format(exc), args.verbose)
            targets = []
        if targets or (time.time() - started) >= args.wait:
            break
        time.sleep(1.0)

    if not targets:
        log("no NOSTATION/Matrix Lab raw-HID device found after {:.0f}s".format(
            time.time() - started), args.verbose)
        return 3

    for d in targets:
        log("target: {}".format(describe(d)), args.verbose)

    now = datetime.datetime.now()
    if args.dry_run:
        log("dry-run: would set {:04d}-{:02d}-{:02d} {} {:02d}:{:02d}:{:02d}".format(
            now.year, now.month, now.day, WEEKDAY_NAMES[now.isoweekday()],
            now.hour, now.minute, now.second), args.verbose)
        return 0

    results = sync_once(targets, now, args.verbose)
    ok_count = sum(1 for r in results if r)
    log("done: {}/{} device(s) synced to {:04d}-{:02d}-{:02d} {} {:02d}:{:02d}:{:02d} in {:.2f}s".format(
        ok_count, len(results), now.year, now.month, now.day,
        WEEKDAY_NAMES[now.isoweekday()], now.hour, now.minute, now.second,
        time.time() - started), args.verbose)
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # never leave a crash invisible under pythonw
        import traceback
        log("FATAL: {}".format(exc))
        log(traceback.format_exc())
        sys.exit(2)
