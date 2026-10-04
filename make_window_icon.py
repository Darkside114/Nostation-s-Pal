#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建时预生成「窗口图标」window-icon.ico。

为什么要在构建时做：这个图标原本是**运行时**用 Pillow 从 companion.ico 里
取最大帧、按 LANCZOS 画成 16/24/32/48/64 的小图再用的。为了那一次缩放，
exe 里就要背上整个 Pillow（约 2.6 MB）。

改成构建时生成后：
  * 运行时的窗口图标质量完全一样（甚至更稳，因为不再依赖 Pillow 存在）
  * exe 可以彻底排除 Pillow，体积明显变小
  * 打包机上本来就有 Pillow（make_icon.py 需要），不增加构建依赖

用法：
    python make_window_icon.py            # 生成 assets/window-icon.ico
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "companion.ico")
DST = os.path.join(HERE, "assets", "window-icon.ico")

# 这几档覆盖 Windows 在高 DPI 标题栏上可能需要的尺寸（175% 缩放下是 56px，
# 会从 64px 那档降采样；有 48/64 两档就够清晰了）
SIZES = (16, 24, 32, 48, 64)


def main():
    from PIL import Image

    if not os.path.exists(SRC):
        print("source icon missing: {}".format(SRC), file=sys.stderr)
        return 1

    with Image.open(SRC) as im:
        best = max(im.info.get("sizes") or [im.size], key=lambda s: s[0])
        frame = im.ico.getimage(best).convert("RGBA")
    print("source largest frame: {}x{}".format(frame.width, frame.height))

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    base = frame.resize((64, 64), Image.LANCZOS)
    base.save(DST, format="ICO", sizes=[(s, s) for s in SIZES])

    print("written: {}  ({} bytes, sizes={})".format(
        DST, os.path.getsize(DST), list(SIZES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
