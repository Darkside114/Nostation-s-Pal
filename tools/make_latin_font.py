"""生成小号拉丁点阵字体（8x10），构建时跑一次，运行时不需要 PIL。

== 为什么要单独做小号字 ==
  屏幕上要放三样东西：区名、温度、湿度。
  * 中文点阵最小可辨认是 12x12，不能再小
  * 但数值如果也用 12 像素高，两行就要 24+ 像素，40 像素的可用高度
    里几乎没有留白，用户反馈"太紧凑"
  所以给数值单独烘一套 8x10 的点阵：数值本身矮一点，
  两行之间就能空出足够距离，版式也接近用户画的示意图。

== 规格 ==
  每字 8 宽 x 10 高，每行 1 字节（低 8 位有效），共 10 字节。
  收录：数字、字母、常用符号。

用法：
    python make_latin_font.py                # 输出 assets/latin8.bin
    python make_latin_font.py --preview "22.0C"
"""
import argparse
import os
import struct
import sys

# 字体选择很讲究：
#   Consolas 是**编程字体，零带斜线**（字模整行都是 ######），
#   在 6x10 这么小的点阵下看起来就像 "0" 被划了一道，用户反馈
#   "22.0C 显示有问题"。所以不能用 Consolas。
#   Arial 的零是干净椭圆，作首选。
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\arial.ttf", 10),
    (r"C:\Windows\Fonts\segoeui.ttf", 10),
    (r"C:\Windows\Fonts\verdana.ttf", 9),
    (r"C:\Windows\Fonts\tahoma.ttf", 10),
    (r"C:\Windows\Fonts\msyh.ttc", 10),
    (r"C:\Windows\Fonts\consola.ttf", 11),
]
# 字模宽度取 6：实测数值要用更窄的步进才放得下
# （"温 -15.5C" + "湿 100%" 本来就快顶满 108px）。
# 右边留白靠下面的 trim 处理。
W, H = 6, 10
ROW_BYTES = 1
CHAR_BYTES = H * ROW_BYTES
MAGIC = b"LAT1"
PAD = 4


# == 手工字形覆盖表 ==
# 有些字符在 6x10 这种极小点阵下**光靠缩放系统字体画不清楚**，
# 只能手工画。当前只有 %：
#   Arial 10px 时右下圈只落在 1~2 个像素上（看着像缺了一半），
#   放大到 12px 又横向溢出 6 像素宽的字模 —— 两头都不行。
#   所以直接给一张手工位图：左上实心圈 + 连续斜线 + 右下实心圈。
OVERRIDES = {
    "%": [
        "......",
        ".##...",
        ".##.#.",
        "....#.",
        "...#..",
        "..#...",
        ".#....",
        ".#.##.",
        ".##...",
        "......",
    ],
}


def charset():
    out = []
    for c in range(0x20, 0x7F):
        out.append(chr(c))
    out += list("°℃%±·—…×÷≈≤≥")
    return out


def render_char(font, ch):
    # 手工覆盖优先
    ov = OVERRIDES.get(ch)
    if ov is not None:
        rows = []
        for y in range(H):
            line = ov[y] if y < len(ov) else ""
            v = 0
            for x in range(W):
                if x < len(line) and line[x] == "#":
                    v |= (1 << x)
            rows.append(v)
        return rows

    from PIL import Image, ImageDraw

    W2 = W + PAD * 2
    H2 = H + PAD * 2
    img = Image.new("L", (W2, H2), 0)
    d = ImageDraw.Draw(img)
    try:
        d.text((PAD, PAD), ch, fill=255, font=font)
    except Exception:
        return None
    bbox = img.getbbox()
    if not bbox:
        return None
    crop = img.crop(bbox)
    # 把左右两侧全空的列裁掉，让字形尽量窄
    cp = crop.load()
    left, right = 0, crop.width - 1
    while left < crop.width and not any(cp[left, y] for y in range(crop.height)):
        left += 1
    while right > left and not any(cp[right, y] for y in range(crop.height)):
        right -= 1
    if right >= left:
        crop = crop.crop((left, 0, right + 1, crop.height))
    if crop.width > W:
        crop = crop.crop((0, 0, W, crop.height))
    out = Image.new("L", (W, H), 0)
    # 数字/字母贴左、垂直居中（比例字体不能水平居中，否则小数点会飘）
    ox = 0
    oy = max(0, (H - crop.height) // 2)
    out.paste(crop, (ox, oy))
    px = out.load()
    rows = []
    any_on = False
    for y in range(H):
        v = 0
        for x in range(W):
            if px[x, y] > 110:
                v |= (1 << x)
                any_on = True
        rows.append(v)
    return rows if any_on else None


def pack_rows(rows):
    data = bytearray()
    for y in range(H):
        data.append(rows[y] & 0xFF if y < len(rows) else 0)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--preview", default=None)
    ap.add_argument("--font", default=None)
    args = ap.parse_args()

    from PIL import ImageFont

    font_path, size = None, None
    if args.font:
        font_path, size = args.font, 11
    else:
        for p, s in FONT_CANDIDATES:
            if os.path.exists(p):
                font_path, size = p, s
                break
    if not font_path:
        print("找不到可用字体")
        return 2
    print("  使用字体: {} @ {}px -> {}x{} 点阵".format(
        os.path.basename(font_path), size, W, H))
    font = ImageFont.truetype(font_path, size)

    glyphs = {}
    missing = []
    for ch in charset():
        r = render_char(font, ch)
        if r is None:
            missing.append(ch)
            continue
        glyphs[ch] = pack_rows(r)
    print("  烘出 {} 个，空白 {} 个".format(len(glyphs), len(missing)))

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "assets", "latin8.bin")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    keys = sorted(glyphs.keys(), key=ord)
    with open(out, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<IHH", len(keys), W, H))
        for ch in keys:
            fh.write(struct.pack("<I", ord(ch)))
            fh.write(glyphs[ch])
    print("  已写出: {}  ({:.1f} KB)".format(out, os.path.getsize(out) / 1024.0))

    if args.preview:
        print()
        for ch in args.preview:
            g = glyphs.get(ch)
            print("    ---- {!r} ----".format(ch))
            if not g:
                print("      缺字")
                continue
            for y in range(H):
                v = g[y]
                print("      " + "".join("#" if (v >> x) & 1 else "."
                                         for x in range(W)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
