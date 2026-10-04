#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nostation 自动同步伴侣 —— 应用图标生成器 (companion.ico).

设计
====
    一块深色霓虹圆角方块, 中央是一只极简表盘; 表盘外围环绕两段开口弧线,
    每段末端收一个实心箭头, 构成顺时针自转的 "Sync" 图形语言。

        "校时 / 同步" 一眼可辨:  表盘 = 时间, 双弧箭头 = 同步 / 循环。

    * 大尺寸 (48~256): 完整矢量渲染 —— 亚光渐层底板 + 霓虹青绿渐变圆环 +
      双箭头 + 表盘指针 / 四点刻度 / 中心轴。
    * 小尺寸 (16/24/32): 独立的 "简化版" 绘制 —— 更粗的圆环、更小的箭头、
      去掉刻度与时针。缩到 16px 仍能读出 "圆环 + 双向箭头" 的清晰剪影,
      而不是把 256px 无脑缩小糊成一团。

输出
====
    companion.ico     16,24,32,48,64,128,256 全部内嵌, PNG 压缩帧, RGBA
    icon-preview.png  浅色底 + 深色底 两行对比预览, 并打印验证结论

渲染管线: Pillow + numpy
    几何 -> 4x 超采样 (标量轮廓线 / numpy 多边形扫描线光栅化)
    渐变 -> numpy 线性 / 径向梯度混色
    曲线 -> Pillow 内置 SVG path 解析器 (贝塞尔), 用于需要解析 path 的场合
    降采样 -> 面积平均 (在预乘 alpha 空间进行, 杜绝边缘灰边)
"""

from __future__ import annotations

import io
import math
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ==========================================================================
# 配置
# ==========================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
ICON_PATH = os.path.join(HERE, "assets", "companion.ico")
PREVIEW_PATH = os.path.join(HERE, "assets", "icon-preview.png")

SIZES = (16, 24, 32, 48, 64, 128, 256)
DETAIL_MIN = 48            # >= 该尺寸使用完整细节版; 更小走简化版
PREVIEW_DISPLAY_PX = 160   # 预览图里每个尺寸统一显示成这么大（否则格子宽窄不一、错位）
TICK_MIN = 128             # >= 该尺寸才画四点刻度
SPARKLE_MIN = 256          # 仅 256 画右上角闪光点缀

SS = 4                     # 超采样倍数
VB = 256.0                 # 设计画布边长 (viewBox)

# --- 调色板 ---------------------------------------------------------------
# 约定: 帧缓冲与所有颜色都是 **0..1 的 float**; 16 进制定义在此处统一归一化。
def _c(hx: int):
    return ((hx >> 16 & 0xFF) / 255.0, (hx >> 8 & 0xFF) / 255.0, (hx & 0xFF) / 255.0)


TILE_TOP = _c(0x3A4558)            # 底板受光侧
TILE_BOT = _c(0x0E131B)            # 底板背光侧
RING_COLS = [                      # 霓虹圆环渐变 (矢量方向 30,20 -> 226,236)
    _c(0x22D3EE),
    _c(0x2BE0C0),
    _c(0x34E08C),
    _c(0x18C8D8),
]
RING_FLAT = _c(0x35E39B)           # 小尺寸单色环 (避免渐变被量化成脏色)
DIAL_COLS = [_c(0x1E2A3B), _c(0x080D14)]   # 表盘径向渐变
DIAL_COLS_SMALL = [_c(0x16202C), _c(0x0A1018)]   # 小尺寸表盘 (更暗以托住亮勾)
HAND_MIN = _c(0xEAFFFB)            # 分针 (最亮)
HAND_HOUR = _c(0x74EFD6)           # 时针 (稍暗, 形成主次)
CHECK_COL = _c(0xF2FFFA)           # 小尺寸里的 "对勾" (分针的替代形态)
TICK_COL = _c(0x6CC4D6)
HUB_COL = _c(0xEAFFFB)
SPARK_COL = _c(0xC4FFF0)
RIM_COL = _c(0xC8E2FF)
GLOSS_COL = _c(0xE0FFFA)

# --- 几何 (256 设计坐标) --------------------------------------------------
CX = CY = 128.0
PIVOT_Y = 122.0                    # 视觉重心 (底板略高于几何中心)
TILE_XY0, TILE_XY1, TILE_R = 8.0, 248.0, 52.0
RING_R = 85.0                      # 圆环中心线半径
DIAL_R = 52.0                      # 表盘半径

# 同步图形: 两段开口弧 + 两个实心箭头, 顺时针自转
GAP = 52.0                         # 断口张角 (度)
A0 = 45.0                          # 断口中心角 (右上 / 左下)
ARROW_L = 28.0                     # 箭头长度 (沿顺时针切线)
ARROW_HW = 30.0                    # 箭头半宽 (> 环半宽 => 箭头内外对称外翻)
ARROW_INSET = 2.0                  # 箭头基座相对断口边缘内缩 (度)
RING_HW_FULL = 15.4                # 完整版圆环半宽
RING_HW_SMALL = 16.5               # 简化版圆环半宽 (略粗, 小尺寸更实)

MINUTE_DEG, MINUTE_LEN, MINUTE_W = 60.0, 42.0, 10.0
HOUR_DEG, HOUR_LEN, HOUR_W = -55.0, 28.0, 11.0

# 小尺寸 (16/24/32) 的 "对勾": 由分针形态演化而来, 尺寸放大以在 16px 下可辨
CHECK_W = 21.0                     # 勾的笔画宽度
CHECK_SMALL_SCALE = 0.62           # 相对设计尺寸的缩放


# ==========================================================================
# 基础几何
# ==========================================================================

def arc_points(a_from: float, a_to: float, r: float, n: int = 220):
    """顺时针从 a_from 扫到 a_to (度)。屏幕坐标 x 右 y 下, 角度 0 = 正上方。"""
    t = np.linspace(math.radians(a_from), math.radians(a_to), n)
    return CX + r * np.sin(t), CY - r * np.cos(t), t


def sync_arcs(gap: float = GAP, center: float = A0, r: float = RING_R):
    """两段开口弧的角区间 [(start, end), ...] (度, 顺时针)。"""
    half = gap / 2.0
    a_start = center - half
    a_end = center + half
    sweep = 360.0 - gap
    return [(a_end, a_start + 360.0), (a_end + 180.0, a_start + 540.0)]


def sync_arrows(length: float = ARROW_L, half_w: float = ARROW_HW,
                gap: float = GAP, center: float = A0, r: float = RING_R,
                inset: float = ARROW_INSET):
    """两个箭头的 3 个顶点 [(tip, base_a, base_b), ...] (设计坐标)。

    箭头以环中心线为轴: 基座落在断口的逆时针边缘, 尖角沿顺时针切线伸出,
    两个侧角分列环的内/外前缘 => 与弧身连成一体, 并明显外翻成箭头。
    """
    tris = []
    for k in (0.0, 180.0):
        a = math.radians((center - gap / 2.0 + inset) + k)
        bx, by = CX + r * math.sin(a), CY - r * math.cos(a)
        tgx, tgy = math.cos(a), math.sin(a)      # 顺时针切线方向
        nx, ny = math.sin(a), -math.cos(a)       # 单位法向
        tris.append((
            (bx + length * tgx, by + length * tgy),
            (bx + half_w * nx, by + half_w * ny),
            (bx - half_w * nx, by - half_w * ny),
        ))
    return tris


def ring_polygon(a0: float, a1: float, r: float, hw: float, n: int = 260):
    """扇形圆环 (开口弧) 的闭合多边形: 外弧正向 + 内弧反向。"""
    t = np.linspace(math.radians(a0), math.radians(a1), n)
    ox, oy = CX + (r + hw) * np.sin(t), CY - (r + hw) * np.cos(t)
    ix, iy = CX + (r - hw) * np.sin(t), CY - (r - hw) * np.cos(t)
    return np.vstack([np.column_stack([ox, oy]),
                      np.column_stack([ix[::-1], iy[::-1]])])


def circle_poly(r: float, n: int = 256, center=(CX, CY)):
    t = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    return np.column_stack([center[0] + r * np.cos(t),
                            center[1] + r * np.sin(t)])


def hand_poly(deg: float, length: float, width: float, n: int = 48,
              pivot=(CX, CY)):
    """胶囊形指针轮廓: 从 pivot 指向 deg 方向, 两端圆头。"""
    a = math.radians(deg)
    dx, dy = math.sin(a), -math.cos(a)
    pxx, pyy = pivot
    hw = width / 2.0
    back = np.column_stack([pxx + hw * np.cos(np.linspace(math.pi / 2, 3 * math.pi / 2, n)),
                            pyy + hw * np.sin(np.linspace(math.pi / 2, 3 * math.pi / 2, n))])
    tipx, tipy = pxx + dx * length, pyy + dy * length
    front = np.column_stack([tipx + hw * np.cos(np.linspace(-math.pi / 2, math.pi / 2, n)),
                             tipy + hw * np.sin(np.linspace(-math.pi / 2, math.pi / 2, n))])
    return np.vstack([back, front])


def stroke_polyline(pts, width: float, n: int = 14):
    """把折线描成有厚度的多边形 (两端与拐角均为圆头)。"""
    hw = width / 2.0
    P = [np.asarray(p, dtype=float) for p in pts]
    left, right, caps = [], [], []
    for i in range(len(P) - 1):
        d = P[i + 1] - P[i]
        L = float(np.hypot(*d)) or 1.0
        u = d / L
        nn = np.array([-u[1], u[0]])
        left += [P[i] + hw * nn, P[i + 1] + hw * nn]
        right += [P[i] - hw * nn, P[i + 1] - hw * nn]

    def cap(p, q):
        d = p - q
        L = float(np.hypot(*d)) or 1.0
        u = d / L
        ang0 = math.atan2(u[1], u[0])
        t = np.linspace(ang0 - math.pi / 2, ang0 + math.pi / 2, n)
        return np.column_stack([p[0] + hw * np.cos(t), p[1] + hw * np.sin(t)])

    caps.append(cap(P[0], P[1]))
    caps.append(cap(P[-1], P[-2]))
    return np.vstack([caps[0], np.asarray(left),
                      caps[1], np.asarray(right)[::-1]])


def check_poly(center, size: float, width: float, tilt: float = 0.0):
    """极简 "对勾" 轮廓 (由时钟分针形态演化), center 为几何中心。"""
    cx, cy = center
    s = size
    a = math.radians(tilt)
    ca, sa = math.cos(a), math.sin(a)

    def rot(px, py):
        dx, dy = px - cx, py - cy
        return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

    p0 = rot(cx - s * 0.50, cy - s * 0.06)
    p1 = rot(cx - s * 0.16, cy + s * 0.34)
    p2 = rot(cx + s * 0.50, cy - s * 0.38)
    return stroke_polyline([p0, p1, p2], width)


def tick_poly(deg: float, r1: float, r2: float, half_w: float):
    """圆盘上的径向刻度 (小矩形, 有厚度)。"""
    a = math.radians(deg)
    ux, uy = math.sin(a), -math.cos(a)     # 径向
    px, py = math.cos(a), math.sin(a)      # 切向
    out = []
    for rr in (r1, r2):
        for sgn in (1.0, -1.0):
            out.append((CX + rr * ux + sgn * half_w * px,
                        CY + rr * uy + sgn * half_w * py))
    # 顺序: r1+, r2+, r2-, r1-  -> 简单四边形
    return [out[0], out[1], out[3], out[2]]


def star_poly(cx: float, cy: float, r_out: float, r_in: float, points: int = 4):
    pts = []
    for i in range(points * 2):
        ang = math.radians(i * (360.0 / (points * 2)))
        rad = r_out if i % 2 == 0 else r_in
        pts.append((cx + rad * math.sin(ang), cy - rad * math.cos(ang)))
    return pts


# ==========================================================================
# 光栅化
# ==========================================================================

def raster_polygon(pts, n: int) -> np.ndarray:
    """把多边形光栅化成 (n,n) 的 float32 覆盖率 (0..1)。

    用 Pillow 的 even-odd 扫描线填充 (正确处理 "外圈 + 反向内圈" 构成的环形),
    再在 2x 更高分辨率上渲染并按面积平均降采样, 得到抗锯齿的覆盖率。
    """
    if n <= 0:
        return np.zeros((0, 0), dtype=np.float32)
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if len(arr) < 3:
        return np.zeros((n, n), dtype=np.float32)
    area2 = float(np.sum(arr[:, 0] * np.roll(arr[:, 1], -1)
                         - np.roll(arr[:, 0], -1) * arr[:, 1]))
    if abs(area2) < 0.05:
        return np.zeros((n, n), dtype=np.float32)

    box = (arr.min(axis=0), arr.max(axis=0))
    if (box[1] - box[0]).max() <= 0.0:
        return np.zeros((n, n), dtype=np.float32)

    ss = 2
    m = n * ss
    img = Image.new("L", (m, m), 0)
    sc = (m - 1) / VB
    ImageDraw.Draw(img).polygon([(p[0] * sc, p[1] * sc) for p in arr], fill=255)
    return (np.asarray(img, dtype=np.float32) / 255.0).reshape(
        n, ss, n, ss).mean(axis=(1, 3))


def combine(*masks) -> np.ndarray:
    """多个覆盖率图层取并集。"""
    out = None
    for m in masks:
        out = m.copy() if out is None else np.maximum(out, m)
    return out if out is not None else np.zeros((0, 0), dtype=np.float32)


def rounded_mask(n: int, xy0: float, xy1: float, radius: float,
                 width: float = 0.0) -> np.ndarray:
    """圆角矩形 (填充 width=0 / 描边 width>0) 的覆盖率蒙版。"""
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)
    sc = n / VB
    box = [xy0 * sc, xy0 * sc, xy1 * sc, xy1 * sc]
    w = max(1, int(round(width * sc))) if width > 0 else 0
    if w > 0:
        d.rounded_rectangle(box, radius=radius * sc, outline=255, width=w)
    else:
        d.rounded_rectangle(box, radius=radius * sc, fill=255)
    return np.asarray(img, dtype=np.float32) / 255.0


# ==========================================================================
# 渐变 (numpy)
# ==========================================================================

def linear_t(xx, yy, angle_deg, pivot, span):
    a = math.radians(angle_deg)
    proj = (xx - pivot[0]) * math.cos(a) + (yy - pivot[1]) * math.sin(a)
    return np.clip(0.5 + proj / span, 0.0, 1.0)


def radial_t(xx, yy, center, radius):
    d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2)
    return np.clip(d / radius, 0.0, 1.0)


def ramp(colors, t):
    """t(0..1) -> 多段线性 RGB 渐变。"""
    stops = np.linspace(0.0, 1.0, len(colors))
    out = np.zeros(t.shape + (3,), dtype=np.float32)
    for c in range(3):
        vals = np.array([col[c] for col in colors], dtype=np.float32)
        out[..., c] = np.interp(t, stops, vals)
    return out


def mix(c0, c1, t):
    a = np.array(c0, dtype=np.float32)
    b = np.array(c1, dtype=np.float32)
    return a * (1.0 - t[..., None]) + b * t[..., None]


def linear_stops(colors, p0, p1, xx, yy):
    ux, uy = p1[0] - p0[0], p1[1] - p0[1]
    ang = math.degrees(math.atan2(uy, ux))
    pivot = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    return ramp(colors, linear_t(xx, yy, ang, pivot, math.hypot(ux, uy)))


# ==========================================================================
# 帧缓冲
# ==========================================================================

class Frame:
    """n x n 的 RGBA 光栅帧 (float32, 非预乘, alpha 0..1)。"""

    def __init__(self, n: int):
        self.n = n
        self.rgb = np.zeros((n, n, 3), dtype=np.float32)
        self.a = np.zeros((n, n), dtype=np.float32)
        s = (np.arange(n) + 0.5) * (VB / n)
        self.xx, self.yy = np.meshgrid(s, s)

    def over(self, mask, color):
        """source-over 合成一个 (mask, color) 图层 (非预乘 alpha, 颜色 0..1)。"""
        ma = np.clip(mask, 0.0, 1.0)
        col = color if isinstance(color, np.ndarray) else np.array(color, dtype=np.float32)
        col = col.astype(np.float32, copy=False)
        if col.ndim == 1:
            col = np.ones(self.a.shape + (1,), dtype=np.float32) * col
        if os.environ.get("ICON_DEBUG") and float(col.max()) > 1.0001:
            raise ValueError(
                f"colour out of 0..1 range (max={float(col.max()):.1f}) - "
                "本文件所有颜色都必须是 0..1 归一化浮点")

        keep = self.a * (1.0 - ma)
        out_a = ma + keep
        num = col * ma[..., None] + self.rgb * keep[..., None]
        safe = np.where(out_a > 1e-6, out_a, 1.0)
        new_rgb = np.where(out_a[..., None] > 1e-6, num / safe[..., None], 0.0)

        # 先全部算完再整体赋值: 否则 self.rgb 会在下一次迭代里读到已覆盖的值
        self.rgb = new_rgb
        self.a = out_a

    def to_image(self) -> Image.Image:
        arr = np.zeros((self.n, self.n, 4), dtype=np.uint8)
        arr[..., :3] = np.clip(self.rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
        arr[..., 3] = np.clip(self.a * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")


def downsample(img: Image.Image, size: int) -> Image.Image:
    """整数倍面积平均降采样 (预乘空间, 避免透明边缘出现灰边)。

    alpha 极小的像素 (边缘) 不参与反预乘除法, 否则分母趋零会把颜色放大成白点;
    这些像素本来就几乎不可见, 直接置 0 由 alpha 控制即可。
    """
    n = img.size[0]
    if n == size:
        return img
    k = n // size
    arr = np.asarray(img, dtype=np.float32) / 255.0
    rgb, a = arr[..., :3], arr[..., 3]
    pm = (rgb * a[..., None]).reshape(size, k, size, k, 3).mean(axis=(1, 3))
    a = a.reshape(size, k, size, k).mean(axis=(1, 3))
    out_rgb = np.zeros_like(pm)
    nz = a > 4.0 / 255.0
    out_rgb[nz] = pm[nz] / a[nz][..., None]
    out_rgb = np.clip(out_rgb, 0.0, 1.0)
    out = np.zeros((size, size, 4), dtype=np.uint8)
    out[..., :3] = np.clip(out_rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


# ==========================================================================
# 主渲染
# ==========================================================================

def tile_palette(size: int):
    """底板配色。小尺寸整体提亮约 1.5 倍, 以便在深色任务栏上仍然是一块 "实体"。"""
    k = 1.0 if size >= DETAIL_MIN else 1.0 + 0.5 * (1.0 - size / DETAIL_MIN)
    top = tuple(min(1.0, c * k) for c in TILE_TOP)
    bot = tuple(min(1.0, c * k) for c in TILE_BOT)
    return top, bot


def render_master(size: int) -> Image.Image:
    """渲染单个尺寸 (内置 SS 倍超采样 + 面积平均抗锯齿)。"""
    n = size * SS
    detailed = size >= DETAIL_MIN

    f = Frame(n)
    xx, yy = f.xx, f.yy
    tile_top, tile_bot = tile_palette(size)

    # 简化版 (16/24/32) 用略粗的环、单色环, 并把 "勾" 放大到能被看清
    hw = RING_HW_FULL if detailed else RING_HW_SMALL

    # ---- 1. 底板: 圆角方块 + 对角渐层 --------------------------------------
    f.over(rounded_mask(n, TILE_XY0, TILE_XY1, TILE_R),
           mix(tile_top, tile_bot, linear_t(xx, yy, 48.0, (CX, CY), VB * 1.15)))

    # ---- 2. 内侧描边高光 (上缘提亮, 让浅色背景下也有立体边界) --------------
    rim = rounded_mask(n, TILE_XY0 + 3.5, TILE_XY1 - 3.5, TILE_R - 3.5, width=1.7)
    fade = np.clip(1.0 - (((xx - CX) * 0.55 + (yy - CY) * 0.95) / 210.0 + 0.20),
                   0.0, 1.0)
    f.over(rim * fade * (0.34 if detailed else 0.24), np.array(RIM_COL, dtype=np.float32))

    # ---- 3. 表盘 ----------------------------------------------------------
    dial_cols = DIAL_COLS if detailed else DIAL_COLS_SMALL
    f.over(raster_polygon(circle_poly(DIAL_R), n),
           ramp(dial_cols, radial_t(xx, yy, (CX, CY - 14.0), DIAL_R * 2.0)))

    # ---- 4. 中央语义: 大尺寸=表针, 小尺寸=粗 "勾" -------------------------
    if detailed:
        f.over(raster_polygon(hand_poly(HOUR_DEG, HOUR_LEN, HOUR_W), n),
               np.array(HAND_HOUR, dtype=np.float32))
        f.over(raster_polygon(hand_poly(MINUTE_DEG, MINUTE_LEN, MINUTE_W), n),
               np.array(HAND_MIN, dtype=np.float32))
    else:
        # 16/24/32: 把 "勾" 放大到约占表盘一半, 保证 1x 下不糊成一团
        scale = CHECK_SMALL_SCALE + (size / float(DETAIL_MIN)) * (1.0 - CHECK_SMALL_SCALE)
        side = 2.0 * DIAL_R * scale
        f.over(raster_polygon(
            check_poly((CX, PIVOT_Y + 4.0), side, CHECK_W * scale), n),
            np.array(CHECK_COL, dtype=np.float32))

    # ---- 5. 同步环: 两段开口弧 + 两个实心箭头 -----------------------------
    ring = combine(*[raster_polygon(ring_polygon(s0, s1, RING_R, hw), n)
                     for s0, s1 in sync_arcs()],
                   *[raster_polygon(list(tri), n) for tri in sync_arrows()])
    if detailed:
        f.over(ring, linear_stops(RING_COLS, (30.0, 20.0), (226.0, 236.0), xx, yy))
    else:
        # 小尺寸用单色亮青绿, 避免渐变被量化成脏色
        f.over(ring, np.array(RING_FLAT, dtype=np.float32))

    # ---- 6. 圆环高光: 上缘提亮, 做出霓虹管的通透感 ------------------------
    gloss = np.clip(1.0 - (yy - 26.0) / 130.0, 0.0, 1.0) ** 2
    f.over(ring * gloss * (0.52 if detailed else 0.34),
           np.array(GLOSS_COL, dtype=np.float32))

    # ---- 7. 表盘四点刻度 (仅大尺寸) ---------------------------------------
    if size >= TICK_MIN:
        ticks = combine(*[raster_polygon(
            tick_poly(deg, DIAL_R - 11.5, DIAL_R - 4.5, 2.05), n)
            for deg in (0.0, 90.0, 180.0, 270.0)])
        f.over(ticks, np.array(TICK_COL, dtype=np.float32))

    # ---- 8. 中心轴点 (小尺寸由 "勾" 本身充当视觉中心, 不再加轴点) ---------
    if detailed:
        f.over(raster_polygon(circle_poly(5.4), n), np.array(HUB_COL, dtype=np.float32))

    # ---- 9. 右上角闪光点缀 (仅 256, 品牌细节) -----------------------------
    if size >= SPARKLE_MIN:
        f.over(raster_polygon(star_poly(222.0, 40.0, 12.0, 4.2), n),
               np.array(SPARK_COL, dtype=np.float32))

    return downsample(f.to_image(), size)


# ==========================================================================
# ICO 写出 (手工封装: 支持逐尺寸图像 + PNG 压缩帧)
# ==========================================================================

def write_ico(path: str, frames: dict) -> list:
    sizes = sorted(frames)
    payloads = []
    for s in sizes:
        buf = io.BytesIO()
        frames[s].save(buf, format="PNG", optimize=True)
        payloads.append(buf.getvalue())

    count = len(sizes)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries, blobs = b"", b""
    for s, data in zip(sizes, payloads):
        b = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)

    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)
    return sizes


def read_ico_directory(path: str):
    with open(path, "rb") as fh:
        raw = fh.read()
    reserved, typ, count = struct.unpack("<HHH", raw[:6])
    out = []
    for i in range(count):
        w, h, cc, res, planes, bpp, ln, off = struct.unpack(
            "<BBBBHHII", raw[6 + 16 * i: 22 + 16 * i])
        out.append((256 if w == 0 else w, 256 if h == 0 else h, bpp, ln, off))
    return reserved, typ, out


def load_frames_from_ico(path: str, sizes) -> dict:
    """从磁盘真实回读每个尺寸 (走 Pillow 的 ICO 解码器)。"""
    out = {}
    for s in sizes:
        with Image.open(path) as im:
            im.size = (s, s)
            im.load()
            out[s] = im.convert("RGBA").copy()
    return out


# ==========================================================================
# 预览图
# ==========================================================================

def _font(px: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    return ImageFont.load_default()


def _display_frame(frame, target):
    """把某一尺寸的帧统一缩放到预览用的显示尺寸。

    16/24/32/48/64 用 NEAREST —— 放大后就是它在屏幕上真实的像素样子，
    能如实暴露小尺寸的清晰度（用平滑插值会把小图"美化"，看着清楚其实不是）。
    128 及以上用 LANCZOS 缩小，画质更好。
    """
    if frame.width == target:
        return frame
    resample = Image.LANCZOS if frame.width > target else Image.NEAREST
    return frame.resize((target, target), resample)


def build_preview(path: str, frames: dict) -> Image.Image:
    light = (0xF4, 0xF6, 0xF9)
    dark = (0x17, 0x1B, 0x22)
    header_h, label_h, pad = 104, 40, 28
    display_px = PREVIEW_DISPLAY_PX          # 每个尺寸统一显示成这么大
    cell_w = display_px + 8 * pad            # 每格一样宽 —— 保证两行对齐
    row_h = display_px + 62 + 12 + 56        # 顶部 62 放标题，底部 56 放注释
    width = pad * 2 + cell_w * len(SIZES)
    height = header_h + (label_h + row_h) * 2 + pad

    sheet = Image.new("RGB", (width, height), (0xFF, 0xFF, 0xFF))
    d = ImageDraw.Draw(sheet)
    f_title = _font(34)
    f_sub = _font(19)
    f_lab = _font(21)
    f_size = _font(21)
    f_note = _font(16)

    d.text((pad, 20), "Nostation Auto Sync Companion  -  companion.ico",
           font=f_title, fill=(0x11, 0x17, 0x20))
    d.text((pad, 66),
           "Size proof on light and dark backgrounds.  All sizes shown at the same "
           "display size; 16 / 24 / 32 use a simplified drawing, 48 px and up full detail.",
           font=f_sub, fill=(0x55, 0x60, 0x70))

    rows = [
        ("Light background", light, (0x24, 0x2C, 0x36), (0xE6, 0xEA, 0xF0)),
        ("Dark background", dark, (0xEC, 0xF1, 0xF7), (0x2C, 0x34, 0x3F)),
    ]
    for ri, (name, bg, fg, chip) in enumerate(rows):
        y0 = header_h + ri * (label_h + row_h)
        d.rectangle([0, y0, width, y0 + label_h], fill=chip)
        d.text((pad, y0 + 9), name, font=f_lab, fill=fg)
        yy = y0 + label_h
        d.rectangle([0, yy, width, yy + row_h], fill=bg)
        for ci, s in enumerate(SIZES):
            cx = pad + ci * cell_w + cell_w // 2
            shown = _display_frame(frames[s], display_px)
            d.text((cx, yy + 18), f"{s} x {s}", font=f_size, fill=fg, anchor="ma")
            sheet.paste(shown, (cx - display_px // 2, yy + 62), shown)
            d.text((cx, yy + 62 + display_px + 14),
                   "simplified" if s < DETAIL_MIN else "full detail",
                   font=f_note, fill=fg, anchor="ma")
            d.text((cx, yy + 62 + display_px + 38),
                   f"native {s}px", font=f_note, fill=fg, anchor="ma")
        d.line([0, yy + row_h, width, yy + row_h], fill=chip, width=2)

    sheet.save(path)
    return sheet


# ==========================================================================
# 验证
# ==========================================================================

def verify(icon_path: str, frames: dict) -> bool:
    line = "=" * 76
    print(line)
    print("VERIFICATION  (every number below is re-read from the file on disk)")
    print(line)

    # --- 1. ICO 目录 ------------------------------------------------------
    reserved, typ, entries = read_ico_directory(icon_path)
    print(f"\n[1] ICO directory  reserved={reserved} type={typ} images={len(entries)}")
    print(f"    {'size':>9}  {'bpp':>4}  {'png bytes':>10}  {'offset':>10}")
    for w, h, bpp, ln, off in entries:
        print(f"    {w:>4}x{h:<4}  {bpp:>4}  {ln:>10}  0x{off:08X}")

    got = sorted(w for w, h, b, l, o in entries)
    want = sorted(SIZES)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    print(f"\n    required sizes : {want}")
    print(f"    sizes in file  : {got}")
    print(f"    missing        : {missing if missing else 'NONE'}")
    print(f"    unexpected     : {extra if extra else 'NONE'}")

    with Image.open(icon_path) as im:
        pil_sizes = sorted(im.info.get("sizes", []))
        n_frames = getattr(im, "n_frames", 1)
    print(f"    Pillow ico.info['sizes'] = {pil_sizes}")
    print(f"    Pillow n_frames          = {n_frames}")
    sizes_ok = (not missing) and (pil_sizes == [(s, s) for s in want])

    # --- 2. alpha 通道 + 四角透明 + 内容存在性 ----------------------------
    print("\n[2] Alpha channel, corner transparency, RGBA integrity, colour richness")
    print(f"    {'size':>5}  {'mode':>5}  {'corners TL,TR,BL,BR':>22}  "
          f"{'a_min':>5} {'a_max':>5}  {'partial':>7}  {'opaque%':>7}  "
          f"{'a>40%':>7}  {'RGB colours':>11}  verdict")
    alpha_ok = True
    for s in want:
        im = frames[s]
        a = np.asarray(im, dtype=np.uint8)[..., 3]
        rgb = im.convert("RGB")
        ncol = len(rgb.getcolors(maxcolors=1 << 22) or [])
        corners = (int(a[0, 0]), int(a[0, -1]), int(a[-1, 0]), int(a[-1, -1]))
        amin, amax = int(a.min()), int(a.max())
        partial = int(((a > 0) & (a < 255)).sum())
        opaque_pct = 100.0 * float((a == 255).sum()) / a.size
        solid_pct = 100.0 * float((a > 40).sum()) / a.size
        # 内容必须真的存在: 颜色远多于纯色块的 1~2 种, 且有不透明区域
        good = (im.mode == "RGBA" and amin == 0 and max(corners) == 0
                and amax == 255 and partial > 0 and ncol > 24 and solid_pct > 55.0)
        alpha_ok &= good
        print(f"    {s:>5}  {im.mode:>5}  {str(corners):>22}  "
              f"{amin:>5} {amax:>5}  {partial:>7}  {opaque_pct:>6.1f}%  "
              f"{solid_pct:>6.1f}%  {ncol:>11}  {'PASS' if good else 'FAIL'}")
    print("    -> corners transparent, real alpha gradient, real content: "
          f"{'PASS' if alpha_ok else 'FAIL'}")

    # --- 3. 小尺寸可辨性 --------------------------------------------------
    print("\n[3] Small-size legibility")
    print(f"    {'size':>5}  {'coverage':>9}  {'centre blob':>11}  {'ring band':>9}  "
          f"{'distinct colours':>16}  render")
    legib_ok = True
    for s in want:
        a = np.asarray(frames[s], dtype=np.uint8)[..., 3].astype(np.float32) / 255.0
        cov = 100.0 * float(a.mean())
        c, h = s // 2, max(2, s // 4)
        cc = 100.0 * float(a[c - h:c + h, c - h:c + h].mean())
        yy, xx = np.mgrid[0:s, 0:s]
        rr = np.sqrt((xx - (s - 1) / 2.0) ** 2 + (yy - (s - 1) / 2.0) ** 2)
        band = (rr > s * 0.26) & (rr < s * 0.42)
        band_cov = 100.0 * float(a[band].mean()) if band.any() else 0.0
        ncol = len(frames[s].convert("RGB").getcolors(maxcolors=1 << 22) or [])
        # 小尺寸判据: 主体实心 (>70%) + 中央有内容 + 环带是实心的
        ok_s = (cov > 70.0 and cc > 85.0 and band_cov > 85.0 and ncol > 24)
        legib_ok &= ok_s
        print(f"    {s:>5}  {cov:>8.1f}%  {cc:>10.1f}%  {band_cov:>8.1f}%  "
              f"{ncol:>16}  {'simplified' if s < DETAIL_MIN else 'full detail'}"
              f"  {'PASS' if ok_s else 'FAIL'}")

    # --- 4. 深/浅背景对比度 (合成到背景后与背景的平均亮度差) --------------
    print("\n[4] Perceived contrast after compositing onto real backgrounds")
    print(f"    {'size':>5}  {'dark bg (24,26,30)':>19}  {'light bg (244,246,249)':>23}  "
          f"{'verdict':>8}")
    bg_dark = np.array([24, 26, 30], dtype=np.float32)
    bg_light = np.array([244, 246, 249], dtype=np.float32)
    contrast_ok = True
    for s in want:
        arr = np.asarray(frames[s].convert("RGBA"), dtype=np.float32)
        al = arr[..., 3:4] / 255.0
        prem = arr[..., :3] * al
        out = []
        for bg in (bg_dark, bg_light):
            comp = prem + bg[None, None, :] * (1.0 - al)
            # 只统计被图标覆盖的区域, 避免大片背景稀释
            m = (al[..., 0] > 0.2)
            out.append(float(np.abs(comp[m] - bg[None, :]).mean()))
        good = min(out) > 12.0
        contrast_ok &= good
        print(f"    {s:>5}  {out[0]:>19.1f}  {out[1]:>23.1f}  "
              f"{'PASS' if good else 'FAIL'}")

    # --- 5. 文件统计 ------------------------------------------------------
    print("\n[5] File stats")
    print(f"    path      : {icon_path}")
    print(f"    file size : {os.path.getsize(icon_path):,} bytes")

    ok = sizes_ok and alpha_ok and legib_ok and contrast_ok
    print(f"\n    OVERALL   : {'PASS' if ok else 'FAIL'}")
    return ok


# ==========================================================================
# main
# ==========================================================================

def main(argv) -> int:
    """生成 companion.ico。

    用法:
        python make_icon.py                 只生成 ICO (build_exe.ps1 会这样调用)
        python make_icon.py --preview       生成 ICO + icon-preview.png
        python make_icon.py --verify        生成 ICO + 完整校验报告 (含预览图)
    """
    args = {a.lower() for a in argv[1:]}
    quiet = not (args & {"--verify", "-v", "--preview", "-p"})

    frames = {}
    for s in SIZES:
        frames[s] = render_master(s)
        if not quiet:
            print(f"  rendered {s:>3}x{s:<3}  "
                  f"({'simplified' if s < DETAIL_MIN else 'full detail'})")

    sizes = write_ico(ICON_PATH, frames)
    print(f"wrote {ICON_PATH}")
    print(f"      {os.path.getsize(ICON_PATH):,} bytes, frames={sizes}")

    if quiet:
        return 0

    disk = load_frames_from_ico(ICON_PATH, SIZES)
    ok = verify(ICON_PATH, disk)

    sheet = build_preview(PREVIEW_PATH, disk)
    print(f"\nwrote {PREVIEW_PATH}")
    print(f"      {os.path.getsize(PREVIEW_PATH):,} bytes, "
          f"sheet={sheet.size[0]}x{sheet.size[1]}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
