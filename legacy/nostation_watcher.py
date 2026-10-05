#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resident watcher: syncs the NOSTATION hub clock whenever it appears.

Why a watcher instead of only Task Scheduler triggers:
  * the hub may be plugged in / powered on after the PC has already booted;
  * the hub may be unplugged and replugged during the day;
  * it catches the case where the hub enumerates a few seconds after logon.

It syncs once per plug-in event (and once at startup if the hub is already
there), so it does not hammer the device.

Usage: pythonw nostation_watcher.py [--interval SECONDS]
"""

import argparse
import datetime
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import nostation_sync as ns  # noqa: E402


def wlog(msg, verbose=False):
    line = "{}  {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if verbose:
        try:
            print(line)
        except Exception:
            pass
    ns._trim_and_append(os.path.join(ns.data_dir(), "nostation-sync.log"), line)


def current_hubs():
    """Set of raw-HID paths that are currently usable targets."""
    try:
        return {d["path"] for d in ns.pick_targets(False, None)}
    except Exception as exc:
        wlog("enumeration failed: {}".format(exc), True)
        return set()


def sync_paths(paths, verbose=False, attempts=3):
    """Sync the given raw-HID paths, retrying while the port settles.

    A freshly enumerated HID interface can refuse the first report; retry a few
    times with a short pause before giving up.
    """
    for attempt in range(1, attempts + 1):
        try:
            targets = [d for d in ns.vial_raw_hid_devices() if d["path"] in paths]
        except Exception as exc:
            wlog("enumeration failed while syncing: {}".format(exc), verbose)
            return
        if not targets:
            wlog("targets vanished before sync (attempt {})".format(attempt), verbose)
            return
        results = ns.sync_once(targets, datetime.datetime.now(), verbose)
        if all(results):
            return
        if attempt < attempts:
            wlog("sync attempt {} failed, retrying".format(attempt), verbose)
            time.sleep(1.5)


def acquire_single_instance_lock(verbose=False):
    """Bind a loopback port as a cross-process lock.

    Returns the socket on success, None if another watcher already holds it.
    Raises OSError if the lock could not be set up at all.
    """
    import socket
    last = None
    for port in range(47823, 47833):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            wlog("instance lock on 127.0.0.1:{}".format(port), verbose)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    if last is not None and getattr(last, "errno", None) in (48, 98, 10048, 10013):
        return None
    raise last if last else OSError("cannot bind lock port")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=3.0,
                        help="how often to check whether the hub appeared/disappeared")
    parser.add_argument("--refresh", type=float, default=1800.0,
                        help="re-sync a connected hub every N seconds (0 disables)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        lock = acquire_single_instance_lock(args.verbose)
    except OSError as exc:
        wlog("lock setup failed ({}), continuing without it".format(exc), args.verbose)
        lock = "unavailable"
    if lock is None:
        wlog("another watcher is already running, exiting", args.verbose)
        return 0

    wlog("watcher start (pid {}, interval {}s)".format(os.getpid(), args.interval), args.verbose)

    known = current_hubs()
    last_sync = 0.0
    if known:
        wlog("hub already present at startup: {} target(s)".format(len(known)), args.verbose)
        sync_paths(known, args.verbose)
        last_sync = time.time()

    while True:
        time.sleep(args.interval)
        try:
            now = current_hubs()
        except Exception as exc:
            wlog("watch loop error: {}".format(exc), args.verbose)
            continue
        appeared = now - known
        if appeared:
            wlog("hub appeared: {} target(s), syncing".format(len(appeared)), args.verbose)
            sync_paths(appeared, args.verbose)
            last_sync = time.time()
            now = current_hubs()
        gone = known - now
        if gone:
            wlog("hub disappeared: {} target(s)".format(len(gone)), args.verbose)
        known = now
        if (args.refresh and known and (time.time() - last_sync) >= args.refresh):
            wlog("periodic refresh of {} target(s)".format(len(known)), args.verbose)
            sync_paths(known, args.verbose)
            last_sync = time.time()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        wlog("FATAL: {}".format(traceback.format_exc()))
        sys.exit(2)
