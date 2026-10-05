"""最小可判读测试：屏幕上只显示一个大词。

为什么用这个：之前用边框/条纹，判断起来有歧义（"疯狂换行"到底是什么意思
很难沟通）。只写一个词就没有歧义了：
  * 尺寸对 -> 能清清楚楚读出这个词
  * 宽度不对 -> 字母会被切碎/错位，读不出来
  * 高度不对 -> 词能读出来，但位置偏上或偏下

同时会把**将要发送的位图**渲染成 PNG 供本地核对，
确保发出去的数据本身是对的（排除"打包出错"这类可能）。

用法：
    python test_word.py HELLO 120x36
    python test_word.py --restore
"""
import os
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
MAGIC = "AUXI"

FONT = {
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


def close_all(dev, n=64):
    for i in range(n):
        xfer(dev, [PREFIX, CLOSE_FILE, i], timeout_ms=120)


def render(word, w, h, scale=2):
    """在 w x h 上从左上方开始写 word（不用边框，避免干扰判读）。"""
    px = [[0] * w for _ in range(h)]
    x = 2
    y = 2
    for ch in word.upper():
        g = FONT.get(ch, FONT[" "])
        for gy in range(7):
            for gx in range(5):
                if g[gy][gx] != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        X, Y = x + gx * scale + sx, y + gy * scale + sy
                        if 0 <= X < w and 0 <= Y < h:
                            px[Y][X] = 1
        x += 6 * scale
    return px


def pack(px, w, h):
    flat = [px[y][x] for y in range(h) for x in range(w)]
    out = bytearray()
    for i in range(len(flat) // 8):
        b = 0
        for bit in range(8):
            if flat[i * 8 + bit]:
                b |= (1 << bit)
        out.append(b)
    return bytes(out)


def save_preview(px, w, h, path, scale=6):
    from PIL import Image
    img = Image.new("RGB", (w * scale, h * scale), (0, 0, 0))
    p = img.load()
    for y in range(h):
        for x in range(w):
            if px[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        p[x * scale + dx, y * scale + dy] = (255, 255, 255)
    img.save(path)
    return path


def upload(dev, px, w, h):
    data = pack(px, w, h)
    HDR = "<4s2HI4H"
    hs = struct.calcsize(HDR)
    off = hs + 2
    payload = (struct.pack(HDR, MAGIC.encode(), hs, off, off + len(data),
                           w, h, 2, 1) + struct.pack("<H", 0) + data)
    close_all(dev)
    r = xfer(dev, struct.pack("BBBB", PREFIX, OPEN_FILE, 0xFF, 0)
             + AUX_FILE.encode())
    if not ack(r, OPEN_FILE):
        return False, "打开失败", len(payload)
    idx = r[3]
    try:
        cur = 0
        while cur < len(payload):
            ch = payload[cur:cur + 24]
            r = xfer(dev, struct.pack("<BBBBI", PREFIX, WRITE_FILE, idx,
                                      len(ch), cur) + ch)
            if not ack(r, WRITE_FILE):
                return False, "写入失败@%d" % cur, len(payload)
            cur += len(ch)
        xfer(dev, struct.pack("BBB", PREFIX, CLOSE_FILE, idx))
        r = xfer(dev, struct.pack("BBB", PREFIX, SET_AUX_MODE, 0))
        return ack(r, SET_AUX_MODE), "已显示", len(payload)
    finally:
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

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    word = args[0] if args else "HELLO"
    size = args[1] if len(args) > 1 else "120x36"
    w, h = (int(x) for x in size.split("x"))

    px = render(word, w, h, scale=2)
    pv = save_preview(px, w, h,
                      os.path.join(os.environ.get("TEMP", "."),
                                   "test_word_preview.png"))
    print("词: %s   尺寸: %dx%d" % (word, w, h))
    print("本地预览: %s" % pv)
    ok, why, total = upload(dev, px, w, h)
    print("上传: %s (%s)  总载荷 %d 字节" % ("成功" if ok else "失败", why, total))
    dev.close()
    print()
    print(">>> 请看屏幕：能读出「%s」这几个字母吗？" % word)
    return 0


if __name__ == "__main__":
    sys.exit(main())
