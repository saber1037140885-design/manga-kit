#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
comic-layout.py —— 把分镜图排成漫画页（PNG + PDF）

用法：
  python scripts/comic-layout.py --src <图目录> --out <输出目录> --mode 4koma

  --src    分镜图目录（按文件名排序取图）
  --out    输出目录
  --mode   4koma（2x2 四格）/ 3rows（纵向三行）/ 1page（一格一页）
  --width  页宽，默认 1480（A5 300dpi 竖版）
  --bleed  页边距，默认 48

气泡语法：图目录下放 dialogue.json
  {
    "001": [{"text": "今晚不打算解释。", "x": 0.62, "y": 0.18, "w": 0.34, "tail": "left"}],
    "005": [{"sfx": "啪叽", "x": 0.30, "y": 0.72, "size": 90, "angle": -12}]
  }
  x/y/w 都是格内相对比例；tail = left/right/none，气泡小尾巴朝向

依赖：Pillow
"""
import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# ---------- 字体 ----------
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",   # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]
SFX_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def load_font(size, bold=True):
    for p in (FONT_CANDIDATES if bold else FONT_CANDIDATES[::-1]):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------- 排版 ----------
def layout_grid(page_w, count, mode, bleed):
    """返回每格的 (x, y, w, h) 列表，全部是页内绝对像素。"""
    inner_w = page_w - bleed * 2
    cells = []

    if mode == "4koma" and count >= 4:
        gap = 22
        cell_w = (inner_w - gap) // 2
        cell_h = cell_w * 4 // 3          # 每格 3:4
        for i in range(4):
            cx = i % 2
            cy = i // 2
            cells.append((bleed + cx * (cell_w + gap),
                          bleed + cy * (cell_h + gap),
                          cell_w, cell_h))
        page_h = bleed * 2 + cell_h * 2 + gap

    elif mode == "3rows":
        gap = 26
        cell_w = inner_w
        cell_h = int(cell_w * 0.62)       # 横向长格
        for i in range(min(3, count)):
            cells.append((bleed, bleed + i * (cell_h + gap), cell_w, cell_h))
        page_h = bleed * 2 + cell_h * min(3, count) + gap * (min(3, count) - 1)

    else:  # 1page
        cell_w = inner_w
        cell_h = int(inner_w * 16 / 9)
        cells.append((bleed, bleed, cell_w, cell_h))
        page_h = bleed * 2 + cell_h

    return cells, page_h


def fit_and_crop(img, w, h):
    """等比缩放后居中裁切到 w×h。"""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def draw_bubble(page, cell, spec, font_cache):
    """在格内画对白气泡。spec: text/x/y/w/tail（相对格）。"""
    cx, cy, cw, ch = cell
    text = spec.get("text", "")
    if not text:
        return
    rx = cx + int(spec.get("x", 0.5) * cw)
    ry = cy + int(spec.get("y", 0.15) * ch)
    bw = int(spec.get("w", 0.4) * cw)

    size = spec.get("size") or max(20, int(cw * 0.052))
    font = font_cache.get(size) or load_font(size)
    font_cache[size] = font

    # 文字换行（按气泡宽度）
    pad = int(size * 0.55)
    max_text_w = bw - pad * 2
    lines, cur = [], ""
    for ch_ in text:
        if font.getlength(cur + ch_) > max_text_w and cur:
            lines.append(cur)
            cur = ch_
        else:
            cur += ch_
    if cur:
        lines.append(cur)

    line_h = int(size * 1.32)
    bh = pad * 2 + line_h * len(lines)
    box = [rx - bw // 2, ry - bh // 2, rx + bw // 2, ry + bh // 2]

    d = ImageDraw.Draw(page, "RGBA")
    d.rounded_rectangle(box, radius=bh // 3, fill=(255, 255, 255, 242),
                        outline=(0, 0, 0, 255), width=3)

    # 小尾巴
    tail = spec.get("tail", "none")
    if tail in ("left", "right"):
        ty = ry + bh // 2 - 6
        if tail == "left":
            tri = [(box[0] + 26, ty), (box[0] - 34, ty + 30), (box[0] + 52, ty + 14)]
        else:
            tri = [(box[2] - 26, ty), (box[2] + 34, ty + 30), (box[2] - 52, ty + 14)]
        d.polygon(tri, fill=(255, 255, 255, 242), outline=(0, 0, 0, 255))

    ty = box[1] + pad
    for ln in lines:
        tw = font.getlength(ln)
        d.text(((rx - tw / 2), ty), ln, font=font, fill=(0, 0, 0, 255))
        ty += line_h


def draw_sfx(page, cell, spec, font_cache):
    """拟声字：白描边 + 黑字，可旋转。先画到透明层再旋转贴回。"""
    cx, cy, cw, ch = cell
    text = spec.get("sfx", "")
    if not text:
        return
    px = cx + int(spec.get("x", 0.5) * cw)
    py = cy + int(spec.get("y", 0.5) * ch)
    size = spec.get("size") or int(cw * 0.13)
    angle = spec.get("angle", -10)
    font = font_cache.get(size) or load_font(size)
    font_cache[size] = font

    tw = int(font.getlength(text)) + 40
    th = int(size * 1.5) + 40
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    lx, ly = 20, 20
    # 描边
    for dx in (-3, -2, -1, 0, 1, 2, 3):
        for dy in (-3, -2, -1, 0, 1, 2, 3):
            if dx or dy:
                ld.text((lx + dx, ly + dy), text, font=font, fill=(255, 255, 255, 255))
    ld.text((lx, ly), text, font=font, fill=(20, 20, 20, 255))

    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    page.alpha_composite(layer, (px - layer.width // 2, py - layer.height // 2))


def build_pages(src, out, mode="4koma", width=1480, bleed=48, per_page=None):
    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    if not files:
        print("no images in", src)
        return []

    dia_path = os.path.join(src, "dialogue.json")
    dialogue = {}
    if os.path.exists(dia_path):
        with open(dia_path, "r", encoding="utf-8-sig") as f:
            dialogue = json.load(f)

    per_page = per_page or {"4koma": 4, "3rows": 3, "1page": 1}[mode]
    os.makedirs(out, exist_ok=True)
    font_cache = {}
    made = []

    for page_idx in range(0, len(files), per_page):
        batch = files[page_idx:page_idx + per_page]
        cells, page_h = layout_grid(width, len(batch), mode, bleed)
        page = Image.new("RGBA", (width, page_h), (255, 255, 255, 255))

        for img_name, cell in zip(batch, cells):
            img = Image.open(os.path.join(src, img_name)).convert("RGB")
            img = fit_and_crop(img, cell[2], cell[3])
            page.paste(img, (cell[0], cell[1]))

            key = os.path.splitext(img_name)[0]
            for spec in dialogue.get(key, []):
                if "sfx" in spec:
                    draw_sfx(page, cell, spec, font_cache)
                else:
                    draw_bubble(page, cell, spec, font_cache)

        # 格线（黑框）
        d = ImageDraw.Draw(page)
        for cell in cells:
            d.rectangle([cell[0], cell[1], cell[0] + cell[2], cell[1] + cell[3]],
                        outline=(0, 0, 0, 255), width=4)

        p = os.path.join(out, "page_%03d.png" % (page_idx // per_page + 1))
        page.convert("RGB").save(p, "PNG")
        made.append(p)
        print("page ->", p, page.size)

    # 合 PDF
    if made:
        imgs = [Image.open(p).convert("RGB") for p in made]
        pdf = os.path.join(out, "comic.pdf")
        imgs[0].save(pdf, "PDF", resolution=300.0, save_all=True, append_images=imgs[1:])
        print("pdf ->", pdf)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="4koma", choices=["4koma", "3rows", "1page"])
    ap.add_argument("--width", type=int, default=1480)
    ap.add_argument("--bleed", type=int, default=48)
    ap.add_argument("--per-page", type=int, default=None)
    a = ap.parse_args()
    build_pages(a.src, a.out, a.mode, a.width, a.bleed, a.per_page)


if __name__ == "__main__":
    main()
