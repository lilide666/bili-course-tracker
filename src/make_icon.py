# -*- coding: utf-8 -*-
"""应用图标生成与转换工具。

- generate_default_icon(): 生成默认炫酷图标（径向渐变 + 播放三角 + 进度弧）
- image_to_ico(data_bytes): 将用户上传的图片转为多尺寸 ico
"""
from io import BytesIO
import os
import sys
from PIL import Image, ImageDraw

# 图标文件保存在数据目录（server.ROOT），与打包版/源码版保持一致
try:
    from server import ROOT as _DIR
except Exception:
    _DIR = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
ICON_FILE = os.path.join(_DIR, "app_icon.ico")
PREVIEW_FILE = os.path.join(_DIR, "app_icon_preview.png")
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def generate_default_icon():
    """生成默认炫酷图标并保存为 ico + png 预览。"""
    size = 256
    cx = cy = size / 2.0
    maxd = (size / 2.0) * 1.4
    # 径向渐变背景：中心蓝紫，边缘深蓝黑
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for y in range(size):
        for x in range(size):
            d = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) / maxd
            t = max(0.0, 1.0 - d)  # 中心1 边缘0
            r = int(10 + (42 - 10) * t)
            g = int(16 + (58 - 16) * t)
            b = int(36 + (110 - 36) * t)
            px[x, y] = (r, g, b, 255)
    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([2, 2, size - 2, size - 2], radius=54, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    d = ImageDraw.Draw(out)
    # 播放三角阴影
    tw, th = size * 0.28, size * 0.34
    ox = cx - tw * 0.30
    sh = [(ox + 4, cy - th * 0.5 + 4), (ox + 4, cy + th * 0.5 + 4), (ox + tw * 0.7 + 4, cy + 4)]
    d.polygon(sh, fill=(0, 0, 0, 110))
    # 播放三角
    tri = [(ox, cy - th * 0.5), (ox, cy + th * 0.5), (ox + tw * 0.7, cy)]
    d.polygon(tri, fill=(255, 255, 255, 255))
    # 装饰进度弧（外圈紫、内圈蓝）
    d.ellipse([size * 0.10, size * 0.10, size * 0.90, size * 0.90],
              outline=(139, 92, 246, 200), width=int(size * 0.035))
    d.ellipse([size * 0.16, size * 0.16, size * 0.84, size * 0.84],
              outline=(110, 168, 255, 120), width=int(size * 0.020))
    out.save(ICON_FILE, format="ICO", sizes=ICO_SIZES)
    out.save(PREVIEW_FILE, format="PNG")
    return True


def image_to_ico(data_bytes):
    """将用户上传的图片字节转为多尺寸 ico + png 预览（居中正方形裁剪 + 圆角）。"""
    im = Image.open(BytesIO(data_bytes)).convert("RGBA")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    im = im.resize((256, 256), RESAMPLE)
    # 统一圆角风格
    mask = Image.new("L", (256, 256), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 255, 255], radius=40, fill=255)
    out = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    out.save(ICON_FILE, format="ICO", sizes=ICO_SIZES)
    out.save(PREVIEW_FILE, format="PNG")
    return True
