"""小号拉丁点阵字库（8x10）读取与绘制，运行时不需要 PIL。

格式（由 tools/make_latin_font.py 生成）：
    magic  "LAT1"                4 字节
    n      字数                  4 字节 (uint32 LE)
    w      单字宽                2 字节
    h      单字高                2 字节
    之后 n 条记录：码点(4 字节) + h 字节位图
        位图按行存，每行 1 字节，最低位是该行最左边的像素。

用途：屏幕上的温度/湿度数值。中文点阵 12x12 太占高度，
数值用 8x10 才能让两行之间留出空白。
"""
import os
import struct
import sys

MAGIC = b"LAT1"


def font_path():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "assets", "latin8.bin")
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "assets", "latin8.bin"),
                 os.path.join(os.path.dirname(here), "assets", "latin8.bin")):
        if os.path.exists(cand):
            return cand
    return os.path.join(here, "assets", "latin8.bin")


class LatinFont:
    """只读的 8x10 点阵字库。"""

    def __init__(self, path=None):
        self.ok = False
        self.w = 8
        self.h = 10
        self._codes = []
        self._data = b""
        self._rec = 0
        self._off = 0
        path = path or font_path()
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except Exception:
            return
        if len(raw) < 12 or raw[:4] != MAGIC:
            return
        n, w, h = struct.unpack_from("<IHH", raw, 4)
        if not (n and w and h):
            return
        self.w, self.h = w, h
        self._rec = 4 + h
        self._data = raw
        self._off = 12
        self._codes = [struct.unpack_from("<I", raw, 12 + i * self._rec)[0]
                       for i in range(n)]
        self.ok = True

    def _find(self, code):
        lo, hi = 0, len(self._codes) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            v = self._codes[mid]
            if v == code:
                return mid
            if v < code:
                lo = mid + 1
            else:
                hi = mid - 1
        return -1

    def glyph_rows(self, ch):
        if not self.ok:
            return None
        i = self._find(ord(ch))
        if i < 0:
            return None
        base = self._off + i * self._rec + 4
        return list(self._data[base:base + self.h])

    # 单字步进：等于字模宽度（字模本身已在生成时裁掉左右空白列）。
    # 从 8 收到 6 是被屏宽逼出来的 —— "温 -15.5C" + "湿 100%"
    # 本来就快顶满 108px，步进 8 会把右栏挤出屏幕。
    # 实参 w 会覆盖它（字模宽度由文件头读出）。
    STEP = 6

    def width_of(self, text, spacing=0):
        n = len(text or "")
        if not n:
            return 0
        step = self.w if self.ok else self.STEP
        return n * step + (n - 1) * spacing

    def draw(self, px, x0, y0, text, color=1, spacing=0):
        """画一行数值。返回结束 x。"""
        h = len(px)
        w = len(px[0]) if h else 0
        x = x0
        for ch in text or "":
            rows = self.glyph_rows(ch)
            for y in range(self.h):
                yy = y0 + y
                if yy < 0 or yy >= h:
                    continue
                v = rows[y] if rows else 0
                for b in range(self.w):
                    if not (v >> b) & 1:
                        continue
                    xx = x + b
                    if 0 <= xx < w:
                        px[yy][xx] = color
            x += (self.w if self.ok else self.STEP) + spacing
        return x - spacing if text else x0


_inst = None


def get_latin(path=None):
    global _inst
    if _inst is None:
        _inst = LatinFont(path)
    return _inst


if __name__ == "__main__":
    f = get_latin()
    print("  字库:", font_path())
    print("  可用:", f.ok, " 字号: {}x{}".format(f.w, f.h),
          " 字数:", len(f._codes))
    for ch in "22.0C%":
        rows = f.glyph_rows(ch)
        print("  --- {!r} ---".format(ch))
        if rows is None:
            print("      缺字")
            continue
        for v in rows:
            print("      " + "".join("#" if (v >> b) & 1 else "."
                                     for b in range(f.w)))
