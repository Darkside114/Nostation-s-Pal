#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PyInstaller 启动闪屏 splash.png（图标 + 产品名）。

闪屏的作用：onefile 的 exe 每次启动都要把内部文件解压到临时目录，
这期间（本机约 1.5~2 秒）用户看到的是一个白框。有了闪屏，
bootloader 一启动就画这张图，白框就没了。
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
ICON = os.path.join(HERE, "assets", "companion.ico")
OUT = os.path.join(HERE, "assets", "splash.png")

BG = (26, 29, 36)
CARD = (34, 38, 46)

W, H = 420, 200
ICON_PX = 128


def load_font(size):
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    # 卡片
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=14, fill=CARD)

    # 图标（取 128 帧）
    icon = Image.open(ICON)
    frame = icon.ico.getimage((128, 128)).convert("RGBA")
    frame = frame.resize((ICON_PX, ICON_PX), Image.LANCZOS)
    canvas.paste(frame, (26, (H - ICON_PX) // 2), frame)

    # 文字
    x = 26 + ICON_PX + 24
    f_title = load_font(27)
    f_sub = load_font(16)
    f_ver = load_font(13)

    d.text((x, 44), "Nostation", font=f_title, fill=(236, 240, 245))
    d.text((x, 80), "自动同步伴侣", font=f_title, fill=(236, 240, 245))

    d.text((x, 122), "开机自动校准 hub 时间", font=f_sub, fill=(150, 160, 172))

    # 版本号（从 companion_app 读，保证一致）
    ver = ""
    try:
        import re
        src = open(os.path.join(HERE, "companion_app.py"), encoding="utf-8").read()
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', src, re.M)
        ver = m.group(1) if m else ""
    except Exception:
        pass
    d.text((x, 148), "v{}   By Darkside".format(ver), font=f_ver, fill=(110, 120, 132))

    canvas.save(OUT)
    print("written:", OUT, canvas.size, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
