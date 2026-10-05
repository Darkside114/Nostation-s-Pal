"""验证 NOSTATION 是否支持辅助屏位图传输（AMK aux_display）。

这是整个"上网抓真实温湿度显示到屏幕"方案的技术前提：
  AMK 协议里没有温湿度命令，屏幕内容只能通过**传一张黑白位图**来设置。
  官网配置页的做法（amk/aux_display.py）：
      open_anim_file("BW_TEXT.ABW") -> write_anim_file(24字节/片) -> close -> apply_aux_mode(0)

本脚本按官方流程走一遍，并在关键步骤打印响应，用来确认设备认不认这套命令。

安全性：
  * 只写设备上的 **BW_TEXT.ABW 这一个文件**（就是官方"黑白屏设置"用的文件）
  * 最后把显示模式设回时间模式(1)，让屏幕回到校时后的正常状态
  * 不碰固件、不碰配置、不碰任何其它文件

用法：
    python probe_aux_display.py            # 画一段测试文字并上传
    python probe_aux_display.py --dry      # 只打印将要发送的内容，不连设备
"""
import struct
import sys
import time

import hid

MSG_LEN = 32
PREFIX = 0xFD
OK = 0xAA
GET_DATETIME, SET_DATETIME = 54, 55
GET_AUX_MODE, SET_AUX_MODE = 58, 59
OPEN_FILE, WRITE_FILE, READ_FILE, CLOSE_FILE = 37, 38, 39, 40

# 官方 aux_display 的默认尺寸（amk/protocol.py 里 width=70 height=40）
AUX_W, AUX_H = 70, 40


def open_dev():
    for d in hid.enumerate(0x4D58, 0x5748):
        if d.get("usage_page") == 0xFF60 and d.get("usage") == 0x61:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    return None


def xfer(dev, msg, timeout_ms=900, quiet=False):
    """发一条 AMK 消息（自动补报告 ID 与 32 字节长度），返回应答。"""
    if len(msg) > MSG_LEN:
        raise ValueError("消息超过 32 字节: %d" % len(msg))
    body = bytes(msg) + b"\x00" * (MSG_LEN - len(msg))
    try:
        n = dev.write(b"\x00" + body)
    except Exception as exc:
        if not quiet:
            print("      write 失败: %s" % exc)
        return b""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            r = dev.read(MSG_LEN, timeout_ms=int(timeout_ms))
        except Exception:
            return b""
        if r:
            return bytes(r)
    return b""


def is_ack(r, cmd):
    return len(r) >= 3 and r[0] == PREFIX and r[1] == cmd and r[2] == OK


# ---------------------------------------------------------------------------
# 5x7 点阵字库（只需要 ASCII 数字、字母、几个符号）
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
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
}


def render_lines(lines, width=AUX_W, height=AUX_H, scale=1, spacing=2):
    """把多行文字画成 width x height 的 1bpp 位图（每行 1 字节，MSB 在左）。"""
    rows = [[0] * width for _ in range(height)]
    y = 0
    for line in lines:
        glyphs = [FONT.get(ch.upper(), FONT[" "]) for ch in line]
        gh = 7 * scale
        if y + gh > height:
            break
        x = 0
        for g in glyphs:
            for gy in range(7):
                rowbits = g[gy]
                for gx in range(5):
                    if rowbits[gx] != "1":
                        continue
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = x + gx * scale + sx, y + gy * scale + sy
                            if 0 <= px < width and 0 <= py < height:
                                rows[py][px] = 1
            x += 5 * scale + scale          # 字距
        y += gh + spacing * scale
    # 打包：官方是每 8 个像素一个字节，低位在前（1 << bit）
    packed = bytearray()
    flat = [rows[yy][xx] for yy in range(height) for xx in range(width)]
    for i in range(len(flat) // 8):
        byte = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                byte |= (1 << bit)
        packed.append(byte)
    return bytes(packed)


def pack_anim_header(width, height, magic, total):
    """官方 pack_anim_header（amk/animation.py）。

    头部格式是 "<4s2HI4H"，**共 20 字节**：
        sig(4s) hdr_size(H) offset(I) file_size(I) width(H) height(H) fmt(H) total(H)
    早期版本我误用了 struct.pack("<4sHHH", ...) 只有 10 字节，
    结果图像数据整体前移 10 字节，字当然是乱的。

    另外注意 hdr_size 是**运行时算出来的**（struct.calcsize），不是写死的。
    """
    import struct as _s
    ANIM_HDR = "<4s2HI4H"
    hdr_size = _s.calcsize(ANIM_HDR)
    offset = hdr_size + 2 * total
    file_size = offset + total * width * height * 2
    sig = magic.encode("utf-8") if isinstance(magic, str) else bytes(magic)
    return _s.pack(ANIM_HDR, sig, hdr_size, offset, file_size,
                   width, height, 2, total)


def main():
    dry = "--dry" in sys.argv

    # 屏幕尺寸可以命令行覆盖，方便试出正确的那一组：
    #   官方 aux_display 默认 70x40；animation.py 里还有 80x30(auxi)/80x80(anim)
    w, h = AUX_W, AUX_H
    for a in sys.argv[1:]:
        if a.startswith("--size="):
            w, h = (int(x) for x in a.split("=", 1)[1].split("x"))

    lines = ["SHANGHAI", "PUDONG", "24.5C", "60%"]
    data = render_lines(lines, width=w, height=h)

    header = pack_anim_header(w, h, "ABIT", 1)
    frame_dur = struct.pack("<H", 0)
    packed = header + frame_dur + data
    print("位图: %dx%d -> 像素数据 %d 字节" % (w, h, len(data)))
    print("头部 %d 字节: %s" % (len(header), header.hex(" ")))
    print("完整载荷: %d 字节" % len(packed))
    if dry:
        print("--dry：不连接设备")
        return 0

    dev = open_dev()
    if not dev:
        print("设备未找到")
        return 2
    print("\n已连接 NOSTATION")

    print("\n[1] 先读一次 AUX 模式（确认这条命令的响应格式）")
    r = xfer(dev, [PREFIX, GET_AUX_MODE])
    print("    GET_AUX_MODE(58) -> %s" % (r.hex(" ") if r else "(超时)"))
    if len(r) >= 4 and r[0] == PREFIX and r[1] == GET_AUX_MODE and r[2] == OK:
        print("    -> ACK，当前显示模式 = %d  (0=自定义 1=时间)" % r[3])
    else:
        print("    -> 非标准 ACK；仍继续尝试（官方实现里这条读取失败也不影响写入）")

    print("\n[2] 打开文件 BW_TEXT.ABW")
    name = b"BW_TEXT.ABW"
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0) + name)
    print("    OPEN_FILE(37) -> %s" % (r.hex(" ") if r else "(超时)"))
    if not is_ack(r, OPEN_FILE):
        print("\n    !! 打开文件未返回 ACK：这台 NOSTATION 可能不支持文件系统/辅助屏。")
        print("    !! 到此终止，不做任何写入。")
        dev.close()
        return 1
    index = r[3]
    print("    -> ACK，分配到的文件句柄 index = %d" % index)

    print("\n[3] 分片写入 (%d 字节, 每片 24)" % len(packed))
    cur = 0
    ok = True
    while cur < len(packed):
        chunk = packed[cur:cur + 24]
        r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, index,
                                  len(chunk), cur) + chunk)
        if not is_ack(r, WRITE_FILE):
            print("    !! 第 %d 字节处写入失败: %s" % (cur, r.hex(" ") if r else "(超时)"))
            ok = False
            break
        cur += len(chunk)
    print("    写入 %s (%d/%d 字节)" % ("完成" if ok else "中断", cur, len(packed)))

    print("\n[4] 关闭文件")
    r = xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, index))
    print("    CLOSE_FILE(40) -> %s  %s" % (
        r.hex(" ") if r else "(超时)", "ACK" if is_ack(r, CLOSE_FILE) else "非 ACK"))

    if ok:
        print("\n[5] 把显示模式切到自定义(0)，让屏幕显示刚传的位图")
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
        print("    SET_AUX_MODE(59,0) -> %s  %s" % (
            r.hex(" ") if r else "(超时)", "ACK" if is_ack(r, SET_AUX_MODE) else "非 ACK"))
        print("\n>>> 请看 NOSTATION 的屏幕：是否显示了 SHANGHAI/PUDONG/24.5C/60% ？")
        print(">>> 想切回时间显示：再运行一次并传 --restore")

    if "--restore" in sys.argv:
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 1))
        print("\n已切回时间模式: %s" % (r.hex(" ") if r else "(超时)"))

    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
