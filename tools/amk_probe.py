"""Matrix Lab / AMK Vial raw-HID probe.

Read-only: finds the keyboard's Vial raw HID interface (usage_page 0xFF60,
usage 0x61) and sends the AMK GET_DATETIME query. Sends no state-changing
command, so it is safe to run repeatedly.
"""
import sys
import time

# Import hidapi before anything that might pull in matplotlib (whose own
# "hid" module would shadow this one).
import hid

MSG_LEN = 32
AMK_PROTOCOL_PREFIX = 0xFD
AMK_PROTOCOL_OK = 0xAA
AMK_PROTOCOL_GET_VERSION = 0x00
AMK_PROTOCOL_GET_DATETIME = 0x36
AMK_PROTOCOL_GET_AUX_MODE = 0x3A
AMK_PROTOCOL_GET_SWITCHTYPE = 0x38

WEEKDAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def raw_hid_paths():
    paths = []
    for d in hid.enumerate():
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            paths.append(d)
    return paths


def describe(d):
    return "{:04x}:{:04x} {} iface={} path={}".format(
        d["vendor_id"], d["product_id"],
        (d.get("product_string") or "?"),
        d.get("interface_number"), d["path"],
    )


def send(dev, payload, timeout_ms=800):
    msg = bytes(payload) + b"\x00" * (MSG_LEN - len(payload))
    n = dev.write(b"\x00" + msg)
    if n != MSG_LEN + 1:
        return None
    data = dev.read(MSG_LEN, timeout_ms=timeout_ms)
    return bytes(data) if data else None


def main():
    paths = raw_hid_paths()
    print("raw HID (0xFF60/0x61) interfaces found: {}".format(len(paths)))
    if not paths:
        print("!! No Vial raw HID interface present. Is the hub plugged in and awake?")
        return 2

    for d in paths:
        print("\n=== {}".format(describe(d)))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            print("   open failed: {}".format(e))
            continue
        try:
            for name, cmd in (("GET_VERSION", AMK_PROTOCOL_GET_VERSION),
                              ("GET_SWITCHTYPE", AMK_PROTOCOL_GET_SWITCHTYPE),
                              ("GET_DATETIME", AMK_PROTOCOL_GET_DATETIME),
                              ("GET_AUX_MODE", AMK_PROTOCOL_GET_AUX_MODE)):
                resp = send(dev, [AMK_PROTOCOL_PREFIX, cmd])
                if resp is None:
                    print("   {:16s} -> timeout".format(name))
                    continue
                ok = resp[2] == AMK_PROTOCOL_OK
                print("   {:16s} -> {} {}".format(
                    name, "OK " if ok else "ERR", resp[:16].hex(" ")))
                if name == "GET_DATETIME" and ok:
                    year = (resp[3] << 8) | resp[4]
                    print("        device clock: {:04d}-{:02d}-{:02d} wd={} {:02d}:{:02d}:{:02d}".format(
                        year, resp[5], resp[6],
                        WEEKDAYS[resp[7]] if 0 < resp[7] < 8 else resp[7],
                        resp[8], resp[9], resp[10]))
                if name == "GET_VERSION" and ok:
                    print("        version bytes: {}".format(resp[3:9].hex(" ")))
                if name == "GET_SWITCHTYPE" and ok:
                    print("        switch_type={}".format(resp[3]))
                if name == "GET_AUX_MODE" and ok:
                    print("        aux_mode={}".format(resp[3]))
                time.sleep(0.05)
        finally:
            dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
