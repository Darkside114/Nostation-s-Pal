"""Read the Vial keyboard definition + Vial ID from the hub (read-only).

Uses the standard Vial raw-HID commands so we can see which AMK features the
firmware advertises (e.g. whether "datetime" is present).
"""
import json
import sys

import hid

MSG_LEN = 32
VIA_PREFIX = 0xFE
CMD_GET_KEYBOARD_ID = 0x00
CMD_GET_SIZE = 0x01
CMD_GET_DEFINITION = 0x02


def send(dev, payload, timeout_ms=1000):
    msg = bytes(payload) + b"\x00" * (MSG_LEN - len(payload))
    dev.write(b"\x00" + msg)
    data = dev.read(MSG_LEN, timeout_ms=timeout_ms)
    return bytes(data) if data else None


def main():
    devs = [d for d in hid.enumerate()
            if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61]
    if not devs:
        print("no raw HID interface")
        return 2

    for d in devs:
        name = d.get("product_string") or "?"
        print("\n=== {:04x}:{:04x} {}".format(d["vendor_id"], d["product_id"], name))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            print("  open failed:", e)
            continue
        try:
            r = send(dev, [VIA_PREFIX, CMD_GET_KEYBOARD_ID])
            print("  keyboard_id:", r.hex(" ") if r else None)
            r = send(dev, [VIA_PREFIX, CMD_GET_SIZE])
            if r and r[0] == VIA_PREFIX:
                size = (r[1] << 8) | r[2]
                print("  definition size:", size)
                blob = b""
                while len(blob) < size:
                    off = len(blob)
                    pkt = [VIA_PREFIX, CMD_GET_DEFINITION, (off >> 8) & 0xFF, off & 0xFF]
                    r = send(dev, pkt)
                    if not r:
                        print("  read timeout at", off)
                        break
                    blob += r[4:]
                try:
                    defn = json.loads(blob[:size].decode("utf-8"))
                except Exception as e:
                    print("  parse failed:", e, blob[:80])
                    continue
                print("  name:", defn.get("name"))
                print("  vendorId/productId: {:04x}/{:04x}".format(
                    defn.get("vendorId", 0), defn.get("productId", 0)))
                print("  vialProtocol:", defn.get("vialProtocol"))
                print("  amkFeature:", json.dumps(defn.get("amkFeature"), ensure_ascii=False))
                print("  keys:", len(defn.get("layouts", {}).get("keymap", [])))
        finally:
            dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
