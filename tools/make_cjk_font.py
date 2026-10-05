"""生成中日韩点阵字体（构建时跑一次，运行时不需要 PIL）。

== 为什么要预烘 ==
  最终 exe 刻意**排除了 PIL**（省 2.6 MB），所以运行时不能靠 Pillow 渲染文字。
  而辅助屏只有 108x40 像素、只有亮/灭两态，本来也用不上矢量字体。
  所以改成：构建时用 PIL + 系统中文字体把需要的字形烘成 1 位点阵，
  存成一个紧凑的二进制文件，运行时直接查表。

== 点阵规格 ==
  每个字 12x12 像素。中文 12px 是最小还能辨认的尺寸 ——
  屏幕可用高度只有 40px，标签 + 数值两行必须控制在 24px 上下，
  所以不能更大。
  存储：每字 12 行，每行 2 字节（低 12 位有效），共 24 字节。

== 收录范围 ==
  1. 界面固定要用的字（地址、温度、湿度、°C 等）
  2. GB2312 一级汉字（最常用 3755 字，覆盖区县名没问题）
  3. 拉丁字母、数字、常用符号（半角，用 6x12 渲染后再并进 12x12 格）

  按码点排序后用二分查找，查到就画、查不到就画一个"缺字"方框，
  绝不会因为缺字而崩。

用法：
    python make_cjk_font.py                 # 输出 assets/cjk12.bin
    python make_cjk_font.py --preview 地址温度湿度
"""
import argparse
import os
import struct
import sys

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\simsun.ttc",    # 宋体
    r"C:\Windows\Fonts\Deng.ttf",      # 等线
]
SIZE = 12
ROWS = SIZE
ROW_BYTES = 2                     # 12 位 -> 2 字节
CHAR_BYTES = ROWS * ROW_BYTES     # 24
MAGIC = b"CJK1"

# 界面里固定会用到的中文（除了这些，其余靠 GB2312 一级字库覆盖）
EXTRA = "地址温度湿度体感气压风力风向天气更新时间显示当前数据源和风免费"
EXTRA += "省市州区县东西南北中路街镇乡村"
# 一些常见但可能不在 GB2312 里的地名/界面用字
EXTRA += "圳莞佛惠珠汕厦漳泉甬绍嘉湖苏锡常镇扬泰淮盐连宿徐"


def gb2312_hanzi():
    """GB2312 的全部汉字（一级 3755 + 二级 3008）。

    为什么要连二级字一起收：一级字只有最常用的 3755 个，
    而地名里常有二级字 —— 例如「深圳」的「圳」就不在一级字里。
    两级合计约 6763 字，按 24 字节/字算约 160 KB，可以接受。
    """
    chars = set()
    for hi in range(0xB0, 0xF8):          # 一级 0xB0-0xD7，二级 0xD8-0xF7
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except Exception:
                continue
            if len(ch) == 1:
                chars.add(ch)
    return sorted(chars)


def ascii_chars():
    out = []
    for c in range(0x20, 0x7F):
        out.append(chr(c))
    out += list("°℃%±·—…×÷≈≤≥")
    return out


# 12px 的中文用 PIL 直接画在 12x12 画布上会偏高（顶部空 2 行、底部被裁），
# 所以先画在略大的画布上，量出实际墨迹范围后再平移进 12x12 格。
PAD = 4


def render_char(font, ch):
    """把一个字渲染成 12x12 的 0/1 矩阵（自动居中）。"""
    from PIL import Image, ImageDraw

    W = SIZE + PAD * 2
    img = Image.new("L", (W, W), 0)
    d = ImageDraw.Draw(img)
    try:
        d.text((PAD, PAD), ch, fill=255, font=font)
    except Exception:
        return None
    # 量墨迹包围盒
    bbox = img.getbbox()
    if not bbox:
        return None
    crop = img.crop(bbox)
    # 放进 12x12 画布并居中
    out = Image.new("L", (SIZE, SIZE), 0)
    ox = max(0, (SIZE - crop.width) // 2)
    oy = max(0, (SIZE - crop.height) // 2)
    out.paste(crop, (ox, oy))
    px = out.load()
    mat = []
    any_on = False
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            v = 1 if px[x, y] > 110 else 0
            row.append(v)
            if v:
                any_on = True
        mat.append(row)
    return mat if any_on else None


def pack_matrix(mat):
    """12 行 x 2 字节。第 y 行低 12 位表示该行从左到右的像素。"""
    data = bytearray()
    for y in range(ROWS):
        row = mat[y] if y < len(mat) else [0] * SIZE
        v = 0
        for x in range(SIZE):
            if row[x]:
                v |= (1 << x)
        data += struct.pack("<H", v)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--preview", default=None)
    ap.add_argument("--font", default=None)
    args = ap.parse_args()

    from PIL import ImageFont

    font_path = args.font
    if not font_path:
        for c in FONT_CANDIDATES:
            if os.path.exists(c):
                font_path = c
                break
    if not font_path:
        print("找不到中文字体")
        return 2
    print("  使用字体:", font_path)

    # 先做一次 12px 渲染确认字体能用
    try:
        font = ImageFont.truetype(font_path, SIZE)
    except Exception as exc:
        print("  字体加载失败:", exc)
        return 2

    charset = set()
    charset.update(ascii_chars())
    charset.update(EXTRA)
    gb = gb2312_hanzi()
    print("  GB2312 汉字: {} 个".format(len(gb)))
    charset.update(gb)
    chars = sorted(c for c in charset if c and c != "\n")
    print("  合计待烘字数: {}".format(len(chars)))

    glyphs = {}
    missing = []
    for i, ch in enumerate(chars):
        m = render_char(font, ch)
        if m is None:
            missing.append(ch)
            continue
        glyphs[ch] = pack_matrix(m)
        if i % 500 == 0 and i:
            print("    已处理 {}/{}".format(i, len(chars)))

    print("  实际烘出: {} 个，空白/失败 {} 个".format(len(glyphs), len(missing)))

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "assets", "cjk12.bin")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    keys = sorted(glyphs.keys(), key=ord)
    with open(out, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<IHH", len(keys), SIZE, ROW_BYTES))
        for ch in keys:
            fh.write(struct.pack("<I", ord(ch)))
            fh.write(glyphs[ch])
    size = os.path.getsize(out)
    print("  已写出: {}  ({:.1f} KB)".format(out, size / 1024.0))
    print("  格式: magic={} 字数={} 字号={}x{} 每字 {} 字节".format(
        MAGIC.decode(), len(keys), SIZE, SIZE, CHAR_BYTES))

    if args.preview:
        prev = args.preview
        print()
        print("  预览: {}".format(prev))
        for ch in prev:
            g = glyphs.get(ch)
            if not g:
                print("    {!r} 缺字".format(ch))
                continue
            print("    ---- {!r} ----".format(ch))
            for y in range(ROWS):
                v = struct.unpack_from("<H", g, y * ROW_BYTES)[0]
                line = "".join("#" if (v >> x) & 1 else "." for x in range(SIZE))
                print("      " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
