#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图标自检：尺寸清单、透明通道、每帧真实内容、深浅背景预览拼图。

用法:
    python verify_icon.py [companion.ico] [-o icon-preview.png]

为什么不用 PIL 的 im.ico.getimage(size)：
    它会挑错帧（实测对同一文件的所有尺寸都返回同一帧），
    于是一个"空白纯色方块"也能一路通过所有检查。所以这里自己按
    ICO 目录偏移逐帧解码。
"""

import argparse
import io
import os
import struct
import sys

from PIL import Image

REQUIRED = [16, 24, 32, 48, 64, 128, 256]
BG_LIGHT = (245, 246, 248)
BG_DARK = (24, 26, 30)
MIN_COLORS = 8          # 少于这个颜色数基本可以断定是空白/纯色块


def decode_frames(path):
    """按 ICO 目录偏移逐帧解码，返回 [(边长, RGBA图像 或 None)]。"""
    data = open(path, "rb").read()
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("不是 ICO 文件")
    count = struct.unpack_from("<H", data, 4)[0]
    frames = []
    off = 6
    for _ in range(count):
        w, h, _n, _r, _p, _bpp, size, offset = struct.unpack_from("<BBBBHHII", data, off)
        off += 16
        blob = data[offset:offset + size]
        im = None
        if blob[:8] == b"\x89PNG\r\n\x1a\n":       # 现代 ICO 用 PNG 存帧
            try:
                im = Image.open(io.BytesIO(blob)).convert("RGBA")
            except Exception:
                im = None
        frames.append(((w or 256), (h or 256), im, size))
    return frames


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("icon", nargs="?", default=os.path.join(here, "..", "assets", "companion.ico"))
    ap.add_argument("-o", "--out", default=os.path.join(here, "..", "assets", "icon-preview.png"))
    args = ap.parse_args()

    if not os.path.exists(args.icon):
        print("找不到图标:", args.icon)
        return 2

    print("图标文件 :", args.icon)
    print("文件大小 : {:,} 字节".format(os.path.getsize(args.icon)))

    frames = decode_frames(args.icon)
    sizes = sorted(s for s, _h, _im, _n in frames)
    print("内含尺寸 :", sizes)
    missing = [s for s in REQUIRED if s not in sizes]
    print("缺失尺寸 :", missing if missing else "无")

    print("\n逐帧内容与透明检查：")
    problems = []
    by_size = {}
    for size, _h, im, nbytes in frames:
        by_size[size] = im
        if im is None:
            print("  {:>3}px  无法解码（非 PNG 帧）".format(size))
            problems.append("{}px 无法解码".format(size))
            continue
        a = im.getchannel("A")
        lo, hi = a.getextrema()
        w, h = im.size
        corners = [a.getpixel((0, 0)), a.getpixel((w - 1, 0)),
                   a.getpixel((0, h - 1)), a.getpixel((w - 1, h - 1))]
        colors = im.convert("RGB").getcolors(maxcolors=200000)
        ncolors = len(colors) if colors else 200000
        alpha_ok = lo < 255 and hi > 0
        corners_ok = all(c == 0 for c in corners)
        content_ok = ncolors >= MIN_COLORS
        if not alpha_ok:
            problems.append("{}px 无真实透明".format(size))
        if not corners_ok:
            problems.append("{}px 四角不透明".format(size))
        if not content_ok:
            problems.append("{}px 只有 {} 种颜色，疑似空白图标".format(size, ncolors))
        print("  {:>3}px  {:,}B  颜色数={:<6} alpha={}-{}  四角={}  {}".format(
            size, nbytes, ncolors, lo, hi, corners,
            "OK" if (alpha_ok and corners_ok and content_ok) else "有问题"))

    # 预览拼图：浅色/深色两行，用真正解码出来的帧
    pad = 14
    cell_h = 256 + pad * 2
    total_w = sum(s + pad * 2 for s in REQUIRED)
    img = Image.new("RGB", (total_w, cell_h * 2), BG_LIGHT)
    for row, bg in enumerate((BG_LIGHT, BG_DARK)):
        x = 0
        for size in REQUIRED:
            frame = by_size.get(size)
            cell = Image.new("RGB", (size + pad * 2, size + pad * 2), bg)
            if frame is not None:
                cell.paste(frame, (pad, pad), frame)
            img.paste(cell, (x, row * cell_h + (cell_h - cell.height) // 2))
            x += size + pad * 2
    img.save(args.out)
    print("\n预览拼图 :", args.out, "(top row = light bg, bottom row = dark bg)")

    if missing or problems:
        print("\n结果: 不合格")
        for p in problems:
            print("  -", p)
        if missing:
            print("  - 缺少尺寸:", missing)
        return 1
    print("\n结果: 合格（尺寸齐全、透明正确、每帧都有实质图形内容）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
