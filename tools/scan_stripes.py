"""横条纹宽度扫描 —— 一次就能看出屏幕真实宽度。

图案：每隔几行画一条整行横线。
  * 宽度猜对 -> 屏幕上是一组**完全水平的平行条纹**
  * 宽度猜错 -> 每行横向错位，条纹变成**斜条纹**

斜条纹一眼可辨，所以这个图案比边框更容易判断。

另外本脚本修正了上传逻辑的一个隐患：
  设备的文件槽位有限，若写入失败而**没有关闭文件**，槽位会被占死，
  之后所有 OPEN_FILE 都会返回 0x55（实测遇到过，句柄索引会变成 48）。
  所以这里每次上传前先关闭所有可能的句柄，并且无论成败都关闭本次句柄。

用法：
    python scan_stripes.py --seq       # 依次试一组尺寸，每个 6 秒
    python scan_stripes.py --restore   # 恢复设备自带画面（模式 5）
    python scan_stripes.py 160x64      # 只试一个
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
SET_AUX_MODE = 0x3B
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 0x25, 0x26, 0x28
AUX_FILE = "BW_TEXT.ABW"
STRIPE_STEP = 4          # 每 4 行一条横线


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    return None


def xfer(dev, msg, timeout_ms=900):
    body = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
    dev.write(b"\x00" + body)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        r = dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
        if r:
            return bytes(r)
    return b""


def ack(r, cmd):
    return len(r) >= 3 and r[0] == PREFIX and r[1] == cmd and r[2] == OK


def close_all(dev, n=64):
    """关闭所有可能的文件句柄，释放被占死的槽位。

    实测教训：设备的文件句柄编号**不是**从 0 开始的小整数（遇到过 48），
    而且只要有一次写入失败没关闭，槽位就会被占死 —— 之后所有
    OPEN_FILE 都返回 0x55，连 DELETE_FILE 也救不回来，只能给设备断电。
    所以每次上传前、后都把所有可能的编号关一遍。
    """
    for i in range(n):
        xfer(dev, [PREFIX, CLOSE_FILE, i], timeout_ms=120)


def stripe_bits(w, h):
    """横条纹：每隔 STRIPE_STEP 行画一条整行线，最左边再加一条竖线。"""
    bits = [0] * (w * h)
    for y in range(0, h, STRIPE_STEP):
        for x in range(w):
            bits[y * w + x] = 1
    for y in range(h):
        bits[y * w] = 1
    return bits


def upload(dev, w, h, msb=False, real_size=True):
    bits = stripe_bits(w, h)
    data = bytearray()
    for i in range(len(bits) // 8):
        b = 0
        for bit in range(8):
            if bits[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        data.append(b)

    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2
    size = off + (len(data) if real_size else w * h * 2)
    payload = (struct.pack(HDR, b"AUXI", hs, off, size, w, h, 2, 1)
               + struct.pack("<H", 0) + bytes(data))

    close_all(dev)
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
             + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败(%s)" % (r.hex(" ")[:18] if r else "超时"), None
    idx = r[3]
    try:
        cur = 0
        while cur < len(payload):
            ch = payload[cur:cur + 24]
            r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx,
                                      len(ch), cur) + ch)
            if not ack(r, WRITE_FILE):
                return False, "写入失败@%d/%d 句柄=%d" % (cur, len(payload),
                                                         idx), idx
            cur += len(ch)
        r = xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
        if not ack(r, CLOSE_FILE):
            return False, "关闭失败", idx
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
        if not ack(r, SET_AUX_MODE):
            return False, "切模式失败", idx
        return True, "%dx%d 数据=%d 总=%d" % (w, h, len(data), len(payload)), idx
    finally:
        # 无论成败都关闭，避免槽位被占死（这是之前设备卡死的原因）
        xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx), timeout_ms=300)
        close_all(dev)


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 5))
        print("已恢复设备自带画面: %s" % ("OK" if ack(r, SET_AUX_MODE) else "失败"))
        dev.close()
        return 0

    if "--seq" in sys.argv:
        cands = [(160, 64), (160, 80), (128, 64), (128, 48), (120, 48),
                 (100, 40), (96, 32), (80, 30), (240, 120), (256, 64)]
        secs = 6
        print("横条纹扫描（每 %d 行一条线），每个显示 %d 秒。" % (STRIPE_STEP, secs))
        print("条纹【水平平行】= 宽度对了；【斜的】= 宽度不对。")
        print()
        for w, h in cands:
            ok, info, _ = upload(dev, w, h)
            print("  -> %-26s %s" % (info, "OK" if ok else "失败"))
            for k in range(secs, 0, -1):
                print("     停留 %2d 秒..." % k, end="\r")
                time.sleep(1)
            print()
        print()
        print("=== 结束 ===")
        print("请告诉我：哪一次的条纹是【水平的】？那就是正确尺寸。")
        dev.close()
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    size = args[0] if args else "160x64"
    w, h = (int(x) for x in size.split("x"))
    ok, info, _ = upload(dev, w, h, msb=("--msb" in sys.argv))
    print("%s -> %s" % (info, "OK" if ok else "失败"))
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
