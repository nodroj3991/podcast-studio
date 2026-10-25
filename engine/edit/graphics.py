"""Title card, lower-third and chapter cards as 1920x1080 PNGs (RGBA) using Pillow.

Brand settings (from the preset)::

    {"primary": "#1F3A5F", "accent": "#E8B04B", "text": "#FFFFFF", "background": "#0F1B2D",
     "font": "Inter", "font_file": "/path/to/Inter-SemiBold.ttf", "podcast_name": "The Academic Journey"}

The renderer overlays the PNGs with ffmpeg (``overlay`` + ``fade``), so they are animated by
ffmpeg (slide/fade) rather than baked in.  Lower-third is rendered *without* a full-frame
background so it can be slid in from the left.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
DEFAULT_BRAND = {"primary": "#1F3A5F", "accent": "#E8B04B", "text": "#FFFFFF", "background": "#0F1B2D",
                 "font_file": "", "podcast_name": ""}


def _hex(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def _font(brand: dict[str, Any], size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [brand.get("font_file_bold" if bold else "font_file"), brand.get("font_file"),
                  "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf" if bold else "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
                  "/System/Library/Fonts/HelveticaNeue.ttc", "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in candidates:
        if c and Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _brand(brand: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULT_BRAND, **(brand or {})}


def _gradient_bg(brand: dict[str, Any]) -> Image.Image:
    img = Image.new("RGBA", (W, H), _hex(brand["background"]))
    top, bottom = _hex(brand["primary"]), _hex(brand["background"])
    px = img.load()
    for y in range(H):
        t = y / H
        col = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,)
        for x in range(0, W, 1):
            px[x, y] = col
    # soft accent glow bottom-left
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-400, H - 500, 700, H + 500), fill=_hex(brand["accent"], 70))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    return Image.alpha_composite(img, glow)


def title_card(out: Path, title: str, guest: str, subtitle: str, brand: dict[str, Any] | None = None,
               episode_label: str = "") -> Path:
    b = _brand(brand)
    img = _gradient_bg(b)
    d = ImageDraw.Draw(img)
    y = 300
    if b.get("podcast_name"):
        d.text((160, 180), b["podcast_name"].upper(), font=_font(b, 34, False), fill=_hex(b["accent"]))
    for line in textwrap.wrap(title, 28)[:3]:
        d.text((160, y), line, font=_font(b, 96), fill=_hex(b["text"]))
        y += 112
    d.rectangle((160, y + 30, 400, y + 38), fill=_hex(b["accent"]))
    d.text((160, y + 80), guest, font=_font(b, 56), fill=_hex(b["text"]))
    d.text((160, y + 150), subtitle, font=_font(b, 36, False), fill=_hex(b["text"], 200))
    if episode_label:
        d.text((160, H - 140), episode_label, font=_font(b, 30, False), fill=_hex(b["text"], 160))
    img.save(out)
    return out


def lower_third(out: Path, name: str, line2: str, brand: dict[str, Any] | None = None) -> Path:
    b = _brand(brand)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f1, f2 = _font(b, 50), _font(b, 32, False)
    tw = max(d.textlength(name, font=f1), d.textlength(line2, font=f2)) + 80
    x0, y0 = 120, H - 260
    d.rounded_rectangle((x0, y0, x0 + tw + 20, y0 + 130), radius=10, fill=_hex(b["primary"], 230))
    d.rectangle((x0, y0, x0 + 14, y0 + 130), fill=_hex(b["accent"]))
    d.text((x0 + 44, y0 + 18), name, font=f1, fill=_hex(b["text"]))
    d.text((x0 + 44, y0 + 80), line2, font=f2, fill=_hex(b["text"], 220))
    img.save(out)
    return out


def chapter_card(out: Path, number: int, title: str, brand: dict[str, Any] | None = None) -> Path:
    b = _brand(brand)
    img = _gradient_bg(b)
    d = ImageDraw.Draw(img)
    d.text((160, 380), f"CHAPTER {number}", font=_font(b, 40, False), fill=_hex(b["accent"]))
    y = 450
    for line in textwrap.wrap(title, 30)[:3]:
        d.text((160, y), line, font=_font(b, 84), fill=_hex(b["text"]))
        y += 100
    img.save(out)
    return out


def thumbnail(out: Path, title: str, guest: str, brand: dict[str, Any] | None = None, still: Path | None = None) -> Path:
    """1280x720 YouTube thumbnail: optional video still on the right, title on the left."""
    b = _brand(brand)
    img = _gradient_bg(b).resize((1280, 720))
    if still and Path(still).exists():
        s = Image.open(still).convert("RGBA")
        s = s.resize((int(s.width * 720 / s.height), 720))
        mask = Image.new("L", s.size, 0)
        md = ImageDraw.Draw(mask)
        for x in range(s.width):
            md.line([(x, 0), (x, s.height)], fill=min(255, max(0, int((x / s.width - 0.25) * 600))))
        img.paste(s, (1280 - s.width, 0), mask)
    d = ImageDraw.Draw(img)
    y = 150
    for line in textwrap.wrap(title, 18)[:3]:
        d.text((70, y), line, font=_font(b, 82), fill=_hex(b["text"]), stroke_width=4, stroke_fill=_hex(b["background"]))
        y += 96
    d.text((70, y + 30), guest, font=_font(b, 44), fill=_hex(b["accent"]), stroke_width=3, stroke_fill=_hex(b["background"]))
    img.convert("RGB").save(out, quality=92)
    return out
