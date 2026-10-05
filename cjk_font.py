"""中文点阵字库读取与绘制（运行时不需要 PIL）。

字库由 tools/make_cjk_font.py 在构建时生成，格式：
    magic  "CJK1"                    4 字节
    n      字数                      4 字节 (uint32 LE)
    size   单字边长                  2 字节 (uint16 LE)
    rb     每行字节数                2 字节 (uint16 LE)
    之后 n 条记录：码点(4 字节) + size*rb 字节位图
        位图按行存储，第 y 行占 rb 字节(little-endian)，
        最低位是这一行最左边的像素。

按码点升序排列，查找用二分。
"""
import os
import struct
import sys

MAGIC = b"CJK1"


def font_path():
    """字库路径。打包后是 _MEIPASS/assets/cjk12.bin。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "assets", "cjk12.bin")
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "assets", "cjk12.bin")
    if os.path.exists(p):
        return p
    # 从 tools/ 之类的位置也试一下
    p = os.path.join(os.path.dirname(here), "assets", "cjk12.bin")
    return p


class CjkFont:
    """只读的 12x12 点阵字库。"""

    def __init__(self, path=None):
        self.ok = False
        self.size = 12
        self.row_bytes = 2
        self._codes = []
        self._data = b""
        self._off = 0
        self._rec = 0
        path = path or font_path()
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except Exception:
            return
        if len(raw) < 12 or raw[:4] != MAGIC:
            return
        n, size, rb = struct.unpack_from("<IHH", raw, 4)
        if not (n and size and rb):
            return
        self.size = size
        self.row_bytes = rb
        self._rec = 4 + size * rb
        self._data = raw
        self._off = 12
        # 码点表（用于二分）
        codes = []
        for i in range(n):
            codes.append(struct.unpack_from("<I", raw, 12 + i * self._rec)[0])
        self._codes = codes
        self.ok = True

    def has(self, ch):
        if not self.ok:
            return False
        return self._find(ord(ch)) >= 0

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
        """返回 size 个整数，每个整数低 size 位表示该行像素（位0=最左）。"""
        if not self.ok:
            return None
        i = self._find(ord(ch))
        if i < 0:
            return None
        base = self._off + i * self._rec + 4
        rows = []
        for y in range(self.size):
            v = struct.unpack_from("<H", self._data, base + y * self.row_bytes)[0]
            rows.append(v)
        return rows

    def draw(self, px, x0, y0, text, color=1, spacing=0):
        """把 text 画到像素矩阵 px 上（px[y][x] = 0/1）。

        越界自动裁剪，所以不用担心画到屏幕外。
        返回画完之后的 x 坐标。
        """
        h = len(px)
        w = len(px[0]) if h else 0
        x = x0
        for ch in text or "":
            rows = self.glyph_rows(ch)
            if rows is None:
                # 缺字：画一个空心方框，至少能看出"这里有个字"
                rows = []
                for y in range(self.size):
                    if y in (0, self.size - 1):
                        rows.append((1 << self.size) - 1)
                    else:
                        rows.append(1 | (1 << (self.size - 1)))
            for y in range(self.size):
                yy = y0 + y
                if yy < 0 or yy >= h:
                    continue
                v = rows[y]
                for b in range(self.size):
                    if not (v >> b) & 1:
                        continue
                    xx = x + b
                    if 0 <= xx < w:
                        px[yy][xx] = color
            x += self.size + spacing
        return x

    @staticmethod
    def text_width(text, spacing=0):
        n = len(text or "")
        if not n:
            return 0
        return n * (12 + spacing) - spacing


_inst = None


def get_font(path=None):
    global _inst
    if _inst is None:
        _inst = CjkFont(path)
    return _inst


if __name__ == "__main__":
    f = get_font()
    print("  字库:", font_path())
    print("  可用:", f.ok, " 字号:", f.size, " 字数:", len(f._codes))
    for ch in "地址温度湿度深圳":
        rows = f.glyph_rows(ch)
        print("  --- {!r} ---".format(ch))
        if rows is None:
            print("      缺字")
            continue
        for v in rows:
            print("      " + "".join("#" if (v >> b) & 1 else "."
                                     for b in range(f.size)))
