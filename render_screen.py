"""用最终确定的参数渲染并上传一屏内容。

== 最终确定的屏幕参数（本文件是权威版本）==
  面板     : 1.51 英寸透明 OLED，物理分辨率 128x56（官方 Notion 文档）
  可用区域 : **108 x 40**（设备 Vial 定义里的 aux_display_params；
             外围被固件自己的装饰边框与 "NOSTATION" 标题占用）
  像素格式 : 1 位/像素，**连续位流**（行与行之间跨字节边界，不补齐）
             -> 108*40/8 = 540 字节，正好等于官方文件的数据量
  文件     : BW_TEXT.ABW
  magic    : "ABIT"
  头部     : pack_anim_header 格式 "<4s2HI4H"，共 20 字节，后接 2 字节帧时长
  上传     : open -> 每片 24 字节写入 -> close -> apply_aux_mode(0)

== 踩过的坑（务必保留）==
  * magic 必须是 "ABIT"（"AUXI" 是另一个动画模式用的）
  * 尺寸必须用 Vial 定义里的 108x40，**不要假设宽度是 8 的倍数**
    （108 不是 8 的倍数，正因为这个错误的假设，正确答案被筛掉了十几轮）
  * 设备写入失败后文件系统会锁死（句柄恒为 48，所有 WRITE 返回 0x55），
    软件无法恢复，必须给设备断电重插。所以上传要稳，别连续盲试。

用法：
    python render_screen.py --text "HELLO"      # 测试文字
    python render_screen.py --temp 19.1 --humid 82   # 温湿度
    python render_screen.py --restore           # 恢复设备自带画面（模式 5）
"""
import argparse
import os
import struct
import sys
import time

import hid

try:
    from cjk_font import get_font
except Exception:                       # 打包/源码两种环境都能导入
    def get_font():
        return None

try:
    from latin_font import get_latin
except Exception:
    def get_latin():
        return None

# ---- 权威屏幕参数 ----
AUX_W, AUX_H = 108, 40
PANEL_W, PANEL_H = 128, 56          # 物理面板（仅供记录）
AUX_MAGIC = "ABIT"
AUX_FILE = "BW_TEXT.ABW"

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
SET_AUX_MODE = 0x3B
OPEN_FILE, WRITE_FILE, CLOSE_FILE = 0x25, 0x26, 0x28
CHUNK = 24
AUX_MODE_CUSTOM = 0
AUX_MODE_BUILTIN = 5                # 设备自带的温湿度画面

FONT = {
    "0": ["01110","10001","10011","10101","11001","10001","01110"],
    "1": ["00100","01100","00100","00100","00100","00100","01110"],
    "2": ["01110","10001","00001","00010","00100","01000","11111"],
    "3": ["11111","00010","00100","00010","00001","10001","01110"],
    "4": ["00010","00110","01010","10010","11111","00010","00010"],
    "5": ["11111","10000","11110","00001","00001","10001","01110"],
    "6": ["00110","01000","10000","11110","10001","10001","01110"],
    "7": ["11111","00001","00010","00100","01000","01000","01000"],
    "8": ["01110","10001","10001","01110","10001","10001","01110"],
    "9": ["01110","10001","10001","01111","00001","00010","01100"],
    "A": ["01110","10001","10001","11111","10001","10001","10001"],
    "B": ["11110","10001","10001","11110","10001","10001","11110"],
    "C": ["01110","10001","10000","10000","10000","10001","01110"],
    "D": ["11100","10010","10001","10001","10001","10010","11100"],
    "E": ["11111","10000","10000","11110","10000","10000","11111"],
    "F": ["11111","10000","10000","11110","10000","10000","10000"],
    "G": ["01110","10001","10000","10111","10001","10001","01111"],
    "H": ["10001","10001","10001","11111","10001","10001","10001"],
    "I": ["01110","00100","00100","00100","00100","00100","01110"],
    "J": ["00111","00010","00010","00010","00010","10010","01100"],
    "K": ["10001","10010","10100","11000","10100","10010","10001"],
    "L": ["10000","10000","10000","10000","10000","10000","11111"],
    "M": ["10001","11011","10101","10101","10001","10001","10001"],
    "N": ["10001","11001","10101","10011","10001","10001","10001"],
    "O": ["01110","10001","10001","10001","10001","10001","01110"],
    "P": ["11110","10001","10001","11110","10000","10000","10000"],
    "Q": ["01110","10001","10001","10001","10101","10010","01101"],
    "R": ["11110","10001","10001","11110","10100","10010","10001"],
    "S": ["01111","10000","10000","01110","00001","00001","11110"],
    "T": ["11111","00100","00100","00100","00100","00100","00100"],
    "U": ["10001","10001","10001","10001","10001","10001","01110"],
    "V": ["10001","10001","10001","10001","10001","01010","00100"],
    "W": ["10001","10001","10001","10101","10101","11011","10001"],
    "X": ["10001","10001","01010","00100","01010","10001","10001"],
    "Y": ["10001","10001","01010","00100","00100","00100","00100"],
    "Z": ["11111","00001","00010","00100","01000","10000","11111"],
    " ": ["00000"]*7,
    ".": ["00000","00000","00000","00000","00000","01100","01100"],
    "-": ["00000","00000","00000","11111","00000","00000","00000"],
    "+": ["00000","00100","00100","11111","00100","00100","00000"],
    "%": ["11001","11010","00010","00100","01000","01011","10011"],
    ":": ["00000","01100","01100","00000","01100","01100","00000"],
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


def blank():
    return [[0] * AUX_W for _ in range(AUX_H)]


def draw(px, x0, y0, text, scale=1):
    x = x0
    for ch in text.upper():
        g = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        X, Y = x + gx * scale + sx, y0 + gy * scale + sy
                        if 0 <= X < AUX_W and 0 <= Y < AUX_H:
                            px[Y][X] = 1
        x += 6 * scale
    return x


def text_width(text, scale=1):
    return len(text) * 6 * scale - scale


def pack(px):
    """连续位流：108x40 全部像素按行优先，行间跨字节边界。"""
    flat = [px[y][x] for y in range(AUX_H) for x in range(AUX_W)]
    out = bytearray()
    for i in range(len(flat) // 8):
        v = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                v |= (1 << bit)
        out.append(v)
    return bytes(out)


def payload_from(px):
    data = pack(px)
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2
    return (struct.pack(HDR, AUX_MAGIC.encode(), hs, off, off + len(data),
                        AUX_W, AUX_H, 2, 1) + struct.pack("<H", 0) + data)


def render_weather(temp, humidity, label=None):
    """温湿度版式（108x40，5x7 字体）：

        PUDONG            <- 区名（可选，小字）
        19.1C   82%       <- 温度大字 + 湿度同列右侧
    """
    px = blank()
    t_str = "%.1fC" % float(temp)
    h_str = "%d%%" % int(round(float(humidity)))

    y = 1
    if label:
        draw(px, 1, y, label[:17], 1)
        y += 9
    else:
        y = 6

    # 温度：选一档放得下的最大字号，并给湿度留出空间
    for sc in (3, 2, 1):
        tw = text_width(t_str, sc)
        hw = text_width(h_str, 1)
        if tw + 4 + hw <= AUX_W - 2:
            break
    draw(px, 1, y, t_str, sc)
    th = 7 * sc
    # 湿度与温度底部对齐
    hx = AUX_W - 1 - text_width(h_str, 1)
    hy = y + max(0, th - 7)
    if hy + 7 > AUX_H:
        hy = AUX_H - 7
    draw(px, max(x_end_guard(t_str, sc) + 2, hx), hy, h_str, 1)
    return px


def x_end_guard(text, scale):
    """温度文字结束的 x 位置（用于避免湿度压到温度上）。"""
    return 1 + text_width(text, scale)


def render_weather_cn(label, temp, humidity):
    """中文版式（用户要求的样式）：

        区名                        <- 第一行
        （留白）
        温度 22.0C      湿度 54%    <- 第二行，左右两栏

    == 版式要点（用户反馈过"太紧凑"）==
      屏幕可用区只有 108x40。要放下三样东西（区名、温度、湿度），
      如果全用 12x12 中文点阵，两行就是 24 像素，40 像素里几乎没留白。
      所以：
        * 区名用中文 12x12（贴顶）
        * 温度/湿度用中文标签(12x12) + **小号数值(8x10)**
        * 第二行贴底，中间靠"区名贴顶 + 数值贴底"自然空出若干像素
      数值用 8x10 是关键 —— 它比 12 矮，整行视觉重量更轻，
      两行之间才腾得出空白。
    """
    px = blank()
    font = get_font()
    lat = get_latin()

    # ---- 第一行：区名（贴顶）------------------------------------------
    if label:
        name = (label or "").strip()
        maxn = max(1, (AUX_W - 2) // font.size) if font.ok else 8
        if len(name) > maxn:
            name = name[:maxn]
        font.draw(px, 1, 0, name)

    # ---- 第二行：温度 / 湿度（贴底）------------------------------------
    t_str = "%.1fC" % float(temp)
    h_str = "%d%%" % int(round(float(humidity)))
    y = AUX_H - font.size               # 中文标签贴底
    if y < 0:
        y = 0
    ny = y + (font.size - lat.h) // 2 if lat.ok else y + 3

    gap = 5                                # 两栏之间至少留的空白
    # == 标签只用**一个字**（温 / 湿）==
    # 实测：中文点阵 12x12，两个字就是 24px，两栏共 48px；
    # 再加上 21~35px 的数值，必然超过 108px 屏宽，右栏会被挤出屏幕。
    # 单字标签后两栏各约 26~37px，才放得下。
    lab1 = "温"
    lab2 = "湿"
    lab_w = font.size                      # 单字标签宽度

    def vw(s):
        return lat.width_of(s) if lat.ok else text_width(s, 1)

    def vdraw(x, s):
        if lat.ok:
            return lat.draw(px, x, ny, s)
        return draw(px, x, ny, s, 1)

    lt_w = lab_w + 1 + vw(t_str)
    rt_w = lab_w + 1 + vw(h_str)

    # 左栏贴左边
    x = 1
    x = font.draw(px, x, y, lab1)
    vdraw(x + 1, t_str)
    left_end = x + 1 + vw(t_str)

    # 右栏贴右边，但不能压到左栏
    rx = AUX_W - 1 - rt_w
    if rx < left_end + gap:
        rx = left_end + gap
    if rx + rt_w > AUX_W - 1:
        rx = max(left_end + gap, AUX_W - 1 - rt_w)
    if rx < 1:
        rx = 1
    x2 = font.draw(px, rx, y, lab2)
    vdraw(x2 + 1, h_str)
    return px


def render_text(text):
    px = blank()
    sc = 3
    while sc > 1 and text_width(text, sc) > AUX_W - 4:
        sc -= 1
    draw(px, 2, (AUX_H - 7 * sc) // 2, text, sc)
    return px


def get_aux_mode(dev):
    """读当前显示模式（只读，用于了解设备状态）。"""
    r = xfer(dev, struct.pack("BB", PREFIX, 0x3A))
    if len(r) >= 4 and r[0] == PREFIX and r[1] == 0x3A:
        return r[3]
    return None


def set_aux_mode(dev, mode):
    r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, mode))
    return ack(r, SET_AUX_MODE)


def upload(dev, payload, switch_mode=None):
    """上传一屏内容。

    == switch_mode 参数（重要）==
      NOSTATION 上有两个**物理按键**（见设备定义）：
          SCR_MOD      "Next screen mode / 切换屏幕模式"
          Screen DISK  "Toggle screen storage / 打开或退出屏幕存储"
      用户就是用 SCR_MOD 在设备的几个画面之间切换的。

      早期版本这里**无条件**发 SET_AUX_MODE(0)，等于每 15 分钟一次的
      天气刷新都会把用户用物理键选的模式**强行抢回模式 0**。
      用户反馈"按了切换屏幕按钮，屏幕显示会变成没预期到的样子"，
      原因就在这里。

      官方工具上传完**不切模式**（见 amk/aux_display.py 的
      on_sync_clicked：只 open/write/close，不调 apply_aux_mode），
      模式完全交给设备和物理键。

      所以这里默认 **switch_mode=None 表示不切模式**；
      调用方明确要求时才传 AUX_MODE_CUSTOM 等值。

    == 关于 CLOSE 的次数 ==
      设备按 OPEN 顺序分配文件句柄，句柄资源有限。这里确保只 CLOSE 一次。
    """
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
             + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开文件失败"
    idx = r[3]
    cur = 0
    failed = False
    while cur < len(payload):
        ch = payload[cur:cur + CHUNK]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx,
                                  len(ch), cur) + ch)
        if not ack(r, WRITE_FILE):
            failed = True
            break
        cur += len(ch)

    # 无论成功失败都只 CLOSE 一次
    r = xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
    closed = ack(r, CLOSE_FILE)
    if failed:
        return False, "写入失败@%d" % cur
    if not closed:
        return False, "关闭失败"

    if switch_mode is None:
        return True, "已写入（未改变显示模式）"
    if not set_aux_mode(dev, switch_mode):
        return False, "切换显示模式失败"
    return True, "已显示"


def save_preview(px, path, scale=6):
    from PIL import Image
    img = Image.new("RGB", (AUX_W * scale, AUX_H * scale), (0, 0, 0))
    p = img.load()
    for y in range(AUX_H):
        for x in range(AUX_W):
            if px[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        p[x * scale + dx, y * scale + dy] = (64, 200, 255)
    img.save(path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=None)
    ap.add_argument("--temp", type=float, default=None)
    ap.add_argument("--humid", type=float, default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    if args.temp is not None:
        px = render_weather(args.temp, args.humid or 0, args.label)
    elif args.text:
        px = render_text(args.text)
    else:
        px = render_text("NOSTATION PAL")

    if args.preview:
        print("预览:", save_preview(px, os.path.join(
            os.environ.get("TEMP", "."), "screen_preview.png")))
        return 0

    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    if args.restore:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, AUX_MODE_BUILTIN))
        print("已恢复设备自带画面: %s" % ("OK" if ack(r, SET_AUX_MODE) else "失败"))
        dev.close()
        return 0

    pay = payload_from(px)
    ok, why = upload(dev, pay)
    dev.close()
    print("屏幕 %dx%d  数据 %d 字节  载荷 %d 字节"
          % (AUX_W, AUX_H, len(pay) - 22, len(pay)))
    print("上传: %s (%s)" % ("成功" if ok else "失败", why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
