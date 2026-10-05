#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOSTATION 辅助屏（80x30 单色）显示模块。

用途：把真实温湿度画成位图并推送到 NOSTATION 的辅助屏，
顶替它自带的不准的传感器读数。

== 为什么是"传位图"而不是"设置温湿度数值" ==
AMK 协议（官方 amk/protocol.py，共 75 条命令）里**没有**设置温湿度的命令；
传感器相关寄存器（RT/TOP/BTM/APC/NOISE_SENS）在本设备上全部返回 0x55（不支持）；
ESP32 命令同样不支持；设备文件系统里也只有屏幕位图文件，没有温湿度数据文件。
固件是自己读传感器、自己画到屏上的，软件无法只改那两个数字。
所以只能"接管整块辅助屏"：把带数字的画面作为位图推上去。
（本条结论有实测记录，见 tools/probe_sensors.py 与 tools/probe_files.py）

== 权威参数（来自官方 amk/animation.py 的 MODES / KEYBOARD_FORMATS）==
    auxi_80_30 : width=80, height=30, magic="AUXI", suffix=".AUX"
    屏幕文件   : BW_TEXT.ABW
早期误用 70x40 + "ABIT"（那是预览控件的默认值）导致屏幕乱码，已修正。
"""
import struct
import time

# ---- 屏幕参数（权威值，勿改）----
AUX_W = 80
AUX_H = 30
AUX_MAGIC = "AUXI"
AUX_FILE = "BW_TEXT.ABW"

# ---- AMK 协议常量 ----
MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
UNSUPPORTED = 0x55
GET_AUX_MODE = 0x3A
SET_AUX_MODE = 0x3B
OPEN_FILE = 0x25
WRITE_FILE = 0x26
CLOSE_FILE = 0x28
CHUNK = 24

MATRIX_LAB_VID = 0x4D58
NOSTATION_PID = 0x5748
USAGE_PAGE = 0xFF60
USAGE = 0x61

# ---- aux 模式 ----
AUX_MODE_CUSTOM = 0      # 显示我们上传的位图
AUX_MODE_BUILTIN = 1     # 设备自带画面（时间/传感器）

# ---------------------------------------------------------------------------
# 5x7 点阵字体：温湿度只需要数字、符号和少量字母
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
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    " ": ["00000"] * 7,
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
    "?": ["01110", "10001", "00001", "00110", "00100", "00000", "00100"],
}

GLYPH_W = 5
GLYPH_H = 7


def text_width(text, scale=1, tracking=1):
    """某段文字在给定缩放下的像素宽度。"""
    n = len(text)
    if n == 0:
        return 0
    return n * GLYPH_W * scale + (n - 1) * tracking * scale


def draw_text(px, x0, y0, text, scale=1, tracking=1):
    """在位图 px（list of list）上画文字。返回结束后的 x。"""
    x = x0
    for ch in text.upper():
        g = FONT.get(ch, FONT[" "])
        for gy in range(GLYPH_H):
            row = g[gy]
            for gx in range(GLYPH_W):
                if row[gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        X = x + gx * scale + sx
                        Y = y0 + gy * scale + sy
                        if 0 <= X < AUX_W and 0 <= Y < AUX_H:
                            px[Y][X] = 1
        x += (GLYPH_W + tracking) * scale
    return x


def render_weather(temp, humidity):
    """把温度/湿度排版成 80x30 位图（list of list of 0/1）。

    布局（80x30，每行约 13 个字符）：
        第 1 行  TEMP 大字（scale=2，字高 14）靠左
        第 2 行  分隔线
        第 3 行  湿度靠右，带 % 号
    温度取一位小数（真实天气数据的精度足够，且 80px 放得下）。
    """
    px = [[0] * AUX_W for _ in range(AUX_H)]

    t_str = "%.1f" % float(temp)
    # 湿度把数字和 % 分开一点，30 像素高的屏上更好认
    h_str = "%d %%" % int(round(float(humidity)))

    # --- 温度：优先用 scale=2 大字，放不下就退回 scale=1 ---
    for scale in (2, 1):
        if text_width(t_str, scale) <= AUX_W - 2:
            break
    ty = 1
    draw_text(px, 1, ty, t_str, scale=scale)
    t_end = 1 + text_width(t_str, scale)

    # 温度右边的单位 C，用大字对齐在温度底部
    if t_end + text_width("C", scale) <= AUX_W:
        draw_text(px, t_end + scale, ty, "C", scale=scale)
    th = GLYPH_H * scale

    # --- 分隔线 ---
    line_y = min(ty + th + 1, AUX_H - 10)
    for x in range(1, AUX_W - 1):
        px[line_y][x] = 1

    # --- 湿度：靠右下角 ---
    hx = max(1, AUX_W - 1 - text_width(h_str, 1))
    hy = min(line_y + 1, AUX_H - GLYPH_H)
    draw_text(px, hx, hy, h_str, scale=1)

    return px


def pack_bitmap(px):
    """把位图打包成设备要的字节流：每字节 8 个像素，低位在前。"""
    flat = [px[y][x] for y in range(AUX_H) for x in range(AUX_W)]
    out = bytearray()
    for i in range(len(flat) // 8):
        b = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                b |= (1 << bit)
        out.append(b)
    return bytes(out)


def pack_header(width, height, magic, total):
    """官方 pack_anim_header：头部 "<4s2HI4H"，共 20 字节。

    字段：sig(4s) hdr_size(H) offset(I) file_size(I)
          width(H) height(H) fmt(H) total(H)
    """
    ANIM_HDR = "<4s2HI4H"
    hdr_size = struct.calcsize(ANIM_HDR)
    offset = hdr_size + 2 * total
    file_size = offset + total * width * height * 2
    return struct.pack(ANIM_HDR, magic.encode(), hdr_size, offset, file_size,
                       width, height, 2, total)


def build_payload(temp, humidity):
    """生成完整的上传载荷：头部 + 帧时长 + 位图数据。"""
    px = render_weather(temp, humidity)
    data = pack_bitmap(px)
    return pack_header(AUX_W, AUX_H, AUX_MAGIC, 1) + struct.pack("<H", 0) + data


# ---------------------------------------------------------------------------
# 与设备通信
# ---------------------------------------------------------------------------
def find_device_paths():
    """返回 NOSTATION 的 raw HID 接口（只认 4d58:5748 + usage 0xFF60/0x61）。"""
    import hid
    out = []
    for d in hid.enumerate(MATRIX_LAB_VID, NOSTATION_PID):
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            out.append(d)
    return out


class AuxScreen:
    """辅助屏读写。只操作 BW_TEXT.ABW 与 aux 显示模式，不碰其它任何东西。"""

    def __init__(self, path=None):
        import hid
        self._hid = hid
        self.dev = hid.device()
        if path is None:
            paths = find_device_paths()
            if not paths:
                raise RuntimeError("未检测到 NOSTATION")
            path = paths[0]["path"]
        self.dev.open_path(path)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _xfer(self, msg, timeout_ms=900):
        body = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
        self.dev.write(b"\x00" + body)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            r = self.dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
            if r:
                return bytes(r)
        return b""

    def _ack(self, r, cmd):
        return len(r) >= 3 and r[0] == PREFIX and r[1] == cmd and r[2] == OK

    def get_aux_mode(self):
        r = self._xfer([PREFIX, GET_AUX_MODE])
        if len(r) >= 4 and r[0] == PREFIX and r[1] == GET_AUX_MODE and r[2] == OK:
            return r[3]
        return None

    def set_aux_mode(self, mode):
        r = self._xfer([PREFIX, SET_AUX_MODE, mode])
        return self._ack(r, SET_AUX_MODE)

    def upload_bitmap(self, payload, set_custom=True):
        """把位图写进设备并切换到自定义显示。返回 (ok, 说明)。"""
        r = self._xfer(struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
                       + AUX_FILE.encode())
        if not self._ack(r, OPEN_FILE):
            return False, "打开屏幕文件失败"
        index = r[3]
        cur = 0
        while cur < len(payload):
            chunk = payload[cur:cur + CHUNK]
            r = self._xfer(struct.pack("<BBBBI", PREFIX, WRITE_FILE, index,
                                       len(chunk), cur) + chunk)
            if not self._ack(r, WRITE_FILE):
                return False, "写入失败于偏移 {}".format(cur)
            cur += len(chunk)
        r = self._xfer(struct.pack("BBB", PREFIX, CLOSE_FILE, index))
        if not self._ack(r, CLOSE_FILE):
            return False, "关闭文件失败"
        if set_custom and not self.set_aux_mode(AUX_MODE_CUSTOM):
            return False, "切换显示模式失败"
        return True, "已显示"

    def show_weather(self, temp, humidity):
        return self.upload_bitmap(build_payload(temp, humidity))

    def show_builtin(self):
        """恢复设备自带画面（时间/传感器）。"""
        return self.set_aux_mode(AUX_MODE_BUILTIN)


# ---------------------------------------------------------------------------
# 预览（开发用）
# ---------------------------------------------------------------------------
def save_preview(temp, humidity, path):
    """把渲染结果存成 PNG，便于开发时核对排版（需要 Pillow，仅开发用）。"""
    from PIL import Image
    px = render_weather(temp, humidity)
    scale = 6
    img = Image.new("RGB", (AUX_W * scale, AUX_H * scale), (0, 0, 0))
    pix = img.load()
    for y in range(AUX_H):
        for x in range(AUX_W):
            if px[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        pix[x * scale + dx, y * scale + dy] = (255, 255, 255)
    img.save(path)
    return path


if __name__ == "__main__":
    import sys
    t = float(sys.argv[1]) if len(sys.argv) > 1 else 23.4
    h = float(sys.argv[2]) if len(sys.argv) > 2 else 68
    out = sys.argv[3] if len(sys.argv) > 3 else "preview.png"
    print("渲染 %.1f C / %d %% -> %s" % (t, int(round(h)), save_preview(t, h, out)))
