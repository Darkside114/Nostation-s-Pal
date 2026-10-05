"""屏幕定标：画出「边框 + 尺寸标记」，用显示效果反推正确的屏幕尺寸。

判断方法：
  * 尺寸猜对 -> 边框闭合成规整矩形，里面能看清尺寸文字
  * 尺寸猜错 -> 每行字节错位，边框断成锯齿/斜线，文字糊成一团

为什么需要它：官方 aux_display.py 里预览控件默认 70x40，但
amk/animation.py 的 MODES 表里 auxi_80_30 是 80x30 —— 两处不一致，
而且这些尺寸本来来自设备的 Vial 定义（本固件不支持读定义），
所以只能靠显示效果实测确定。

用法：
    python calibrate_screen.py 80x30        # 只测一个尺寸
    python calibrate_screen.py --seq        # 自动轮换候选（每个停 8 秒）
    python calibrate_screen.py --restore    # 恢复设备自带画面（模式 5）
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
MAGIC = "AUXI"                 # 官方 auxi_80_30 用的 magic

FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    " ": ["00000"] * 7,
}


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


def make_frame(w, h, label):
    """粗边框 + 底边缺口 + 大号尺寸文字。"""
    px = [[0] * w for _ in range(h)]
    t = 2 if min(w, h) > 34 else 1
    for y in range(h):
        for x in range(w):
            if x < t or x >= w - t or y < t or y >= h - t:
                px[y][x] = 1
    gap = max(4, w // 6)
    for y in range(h - t, h):
        for x in range((w - gap) // 2, (w + gap) // 2):
            px[y][x] = 0
    text = label.upper()
    scale = 1
    for s in (2, 1):
        tw = sum(6 * s for _ in text) - s
        if tw <= w - 2 * t and 7 * s <= h - 2 * t:
            scale = s
            break
    tw = sum(6 * scale for _ in text) - scale
    ox = max(t, (w - tw) // 2)
    oy = max(t, (h - 7 * scale) // 2)
    x = ox
    for ch in text:
        g = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        X, Y = x + gx * scale + sx, oy + gy * scale + sy
                        if 0 <= X < w and 0 <= Y < h and px[Y][X] == 0:
                            px[Y][X] = 1
        x += 6 * scale
    return px


def pack(px, w, h, msb=False):
    """每字节 8 个像素。默认低位在前（与官方 aux_display 一致）。"""
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        b = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(b)
    return bytes(out)


def header(w, h, magic, total, data_len=None):
    """官方 pack_anim_header：头部 "<4s2HI4H" 共 20 字节。

    注意 file_size 这一项：官方写法是 offset + total*w*h*2（那个 ×2 是给
    RGB565 动画用的）。但单色辅助屏的实际数据只有 w*h/8 字节，
    80x30 只有 300 字节，而官方公式算出 4822 字节 —— 两者差很多。
    如果固件会校验这个字段，就会读错数据。

    所以这里可以指定 data_len 来写入"真实文件大小"，用于对比测试。
    """
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2 * total
    if data_len is None:
        size = off + total * w * h * 2          # 官方原式
    else:
        size = off + data_len                   # 真实大小
    return struct.pack(HDR, magic.encode(), hs, off, size, w, h, 2, total)


def upload(dev, w, h, label, magic=MAGIC, real_size=False, msb=False):
    px = make_frame(w, h, label)
    data = pack(px, w, h, msb=msb)
    hdr = header(w, h, magic, 1, data_len=(len(data) if real_size else None))
    payload = hdr + struct.pack("<H", 0) + data
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        print("    打开文件失败: %s" % (r.hex(" ") if r else "超时"))
        return False
    index = r[3]
    cur = 0
    while cur < len(payload):
        chunk = payload[cur:cur + 24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, index,
                                  len(chunk), cur) + chunk)
        if not ack(r, WRITE_FILE):
            print("    写入失败于 %d" % cur)
            return False
        cur += len(chunk)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    print("    %dx%d magic=%s 数据=%d 总载荷=%d file_size=%s 位序=%s 已显示=%s"
          % (w, h, magic, len(data), len(payload),
             "真实" if real_size else "官方原式",
             "MSB" if msb else "LSB", ack(r, SET_AUX_MODE)))
    return True


def main():
    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 5))
        print("已恢复设备自带画面（模式 5）: %s"
              % ("OK" if ack(r, SET_AUX_MODE) else r.hex(" ")))
        dev.close()
        return 0

    if "--seq" in sys.argv:
        # 一次轮换把三个可疑点全部覆盖：尺寸、file_size 字段、位序
        cands = [
            (80, 30, False, False),
            (80, 30, True, False),
            (80, 30, False, True),
            (80, 30, True, True),
            (70, 40, True, False),
        ]
        secs = 8
        print("轮换测试（尺寸 / file_size 字段 / 位序），每个显示 %d 秒：" % secs)
        for w, h, real, msb in cands:
            tag = "%dX%d%s%s" % (w, h, "R" if real else "", "M" if msb else "")
            print("  -> %s" % tag)
            upload(dev, w, h, "%dX%d" % (w, h), real_size=real, msb=msb)
            for k in range(secs, 0, -1):
                print("     停留 %2d 秒..." % k, end="\r")
                time.sleep(1)
            print()
        print()
        print("=== 轮换结束 ===")
        print("请告诉我：哪一版显示成了【闭合的规整矩形】？")
        print("（矩形里会写着尺寸，例如 80X30）")
        dev.close()
        return 0

    size = [a for a in sys.argv[1:] if not a.startswith("--")] or ["80x30"]
    w, h = (int(x) for x in size[0].split("x"))
    real = "--real-size" in sys.argv
    msb = "--msb" in sys.argv
    print("上传 %dx%d (file_size=%s, 位序=%s) ..."
          % (w, h, "真实" if real else "官方原式", "MSB" if msb else "LSB"))
    upload(dev, w, h, "%dX%d" % (w, h), real_size=real, msb=msb)
    dev.close()
    print()
    print(">>> 若显示为闭合矩形 -> 参数正确；若边框呈锯齿/斜线 -> 参数不对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
