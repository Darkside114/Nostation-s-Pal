"""把温湿度渲染成位图，按**官方权威参数**传到 NOSTATION 辅助屏。

官方参数（amk/animation.py 的 MODES / KEYBOARD_FORMATS）：
    auxi_80_30 = 宽 80, 高 30, magic "AUXI", 后缀 .AUX
    文件: BW_TEXT.ABW（辅助屏自定义画面）

之前的错误：用了 70x40 + magic "ABIT"（那是预览控件的默认值，不是设备参数），
所以屏幕一直是乱码。

用法：
    python upload_weather.py --temp 19.1 --humid 82 --label 浦东新区
    python upload_weather.py --demo          # 内置一组演示数值
    python upload_weather.py --restore       # 恢复设备自带显示
"""
import argparse
import datetime
import struct
import sys
import time

import hid

# ---- 官方权威参数 ----
AUX_W, AUX_H = 80, 30
AUX_MAGIC = "AUXI"
AUX_FILE = "BW_TEXT.ABW"

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_AUX_MODE, SET_AUX_MODE = 0x3A, 0x3B
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 0x25, 0x26, 0x28
CHUNK = 24


# ---------------------------------------------------------------------------
# 5x7 点阵字体（ASCII）
# ---------------------------------------------------------------------------
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
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    " ": ["00000"] * 7,
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "01100", "01100", "01000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
}


def draw_text(px, w, h, x0, y0, text, scale=1):
    """把 text 画到 px 位图上（就地修改），返回结束时的 x。"""
    x = x0
    for ch in text.upper():
        g = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px_x = x + gx * scale + sx
                        px_y = y0 + gy * scale + sy
                        if 0 <= px_x < w and 0 <= px_y < h:
                            px[px_y][px_x] = 1
        x += 6 * scale
    return x


def pack_bitmap(px, w, h, msb=False):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        b = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                b |= (1 << (7 - bit)) if msb else (1 << bit)
        out.append(b)
    return bytes(out)


def pack_header(w, h, magic, total):
    """官方 pack_anim_header：头部 "<4s2HI4H" 共 20 字节。"""
    ANIM_HDR = "<4s2HI4H"
    hdr = struct.calcsize(ANIM_HDR)
    offset = hdr + 2 * total
    size = offset + total * w * h * 2
    return struct.pack(ANIM_HDR, magic.encode(), hdr, offset, size,
                       w, h, 2, total)


def build_frame(temp, humid, label, msb=False):
    """生成一屏内容：上行地点，中间大号温度，下行湿度。"""
    w, h = AUX_W, AUX_H
    px = [[0] * w for _ in range(h)]

    t_str = "%s C" % ("%.1f" % temp)
    h_str = "%d %%" % int(round(humid))

    # 有地点就画一行小字在最上面，否则只显示温湿度
    y = 1
    if label:
        draw_text(px, w, h, 1, y, label[:12], scale=1)
        y += 9
    # 温度用大号字（scale=2 → 14 像素高）
    if y + 14 <= h:
        draw_text(px, w, h, 1, y, t_str, scale=2)
    else:
        draw_text(px, w, h, 1, y, t_str, scale=1)
    # 湿度放最后一行
    draw_text(px, w, h, w - (len(h_str) * 6), h - 8, h_str, scale=1)
    return pack_bitmap(px, w, h, msb)


# ---------------------------------------------------------------------------
# 设备通信
# ---------------------------------------------------------------------------
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


def upload(dev, data, magic=AUX_MAGIC, msb=False):
    payload = pack_header(AUX_W, AUX_H, magic, 1) + struct.pack("<H", 0) + data
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开文件失败: %s" % (r.hex(" ") if r else "超时")
    index = r[3]
    cur = 0
    while cur < len(payload):
        chunk = payload[cur:cur + CHUNK]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, index,
                                  len(chunk), cur) + chunk)
        if not ack(r, WRITE_FILE):
            return False, "写入失败于 %d" % cur
        cur += len(chunk)
    xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
    return ack(r, SET_AUX_MODE), "已切到自定义显示"


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--temp", type=float, default=None)
    ap.add_argument("--humid", type=float, default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--msb", action="store_true")
    ap.add_argument("--magic", default=AUX_MAGIC)
    args = ap.parse_args()

    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    print("已连接 NOSTATION  (辅助屏参数 %dx%d, magic %r)"
          % (AUX_W, AUX_H, args.magic))

    if args.restore:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 1))
        print("已恢复设备自带显示: %s" % ("OK" if ack(r, SET_AUX_MODE)
                                          else r.hex(" ")))
        dev.close()
        return 0

    temp = args.temp if args.temp is not None else 23.4
    humid = args.humid if args.humid is not None else 68.0
    label = args.label or ("DEMO" if args.demo else "")
    if not label:
        label = "WEATHER"
    print("内容: %s  %.1f C  %d %%" % (label, temp, int(round(humid))))

    data = build_frame(temp, humid, label, args.msb)
    print("位图数据 %d 字节 (期望 %d)" % (len(data), AUX_W * AUX_H // 8))
    ok, why = upload(dev, data, args.magic, args.msb)
    print("上传结果: %s  (%s)" % ("成功" if ok else "失败", why))
    dev.close()
    print()
    print(">>> 请看屏幕。若这次显示正常（能看清 WEATHER / 23.4 C / 68 %），")
    print(">>> 说明 80x30 + AUXI 是对的，我就按这个正式实现天气功能。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
