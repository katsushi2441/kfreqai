#!/usr/bin/env python3
"""Generate the default OGP image for the Kurage AI trading blog.

Composites the existing kurage_avatar_smile.png character art onto a
branded gradient background matching the kurage_knowledge.php color
palette (sea/cyan), with the blog title overlaid.
"""
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ASSETS_DIR = "/tmp/kurage_assets"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blog-bludit", "bl-themes", "kurage", "img", "ogp-default.png")

W, H = 1200, 630

FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def make_gradient(w, h, top, bottom):
    base = Image.new("RGB", (w, h), top)
    top_c = Image.new("RGB", (w, h), top)
    bottom_c = Image.new("RGB", (w, h), bottom)
    mask = Image.new("L", (w, h))
    mask_data = []
    for y in range(h):
        mask_data.extend([int(255 * (y / h))] * w)
    mask.putdata(mask_data)
    base.paste(bottom_c, (0, 0), mask)
    return base


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    bg = make_gradient(W, H, (237, 251, 255), (235, 255, 245))
    draw = ImageDraw.Draw(bg)

    # Soft radial glow blobs (approximate the hero radial-gradient look)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([-150, -200, 650, 400], fill=(85, 199, 218, 60))
    gdraw.ellipse([850, -150, 1350, 300], fill=(146, 230, 250, 50))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    bg = Image.alpha_composite(bg.convert("RGBA"), glow)
    draw = ImageDraw.Draw(bg)

    # Character art on the right side
    char = Image.open(os.path.join(ASSETS_DIR, "kurage_avatar_smile.png")).convert("RGBA")
    target_h = 620
    ratio = target_h / char.height
    char = char.resize((int(char.width * ratio), target_h))
    bg.paste(char, (W - char.width + 60, H - target_h), char)

    # Text block on the left
    title_font = ImageFont.truetype(FONT_BOLD, 64)
    sub_font = ImageFont.truetype(FONT_REGULAR, 30)
    eyebrow_font = ImageFont.truetype(FONT_BOLD, 24)

    accent = (42, 168, 199)
    ink = (23, 50, 77)
    muted = (63, 98, 122)

    ex, ey = 70, 120
    draw.ellipse([ex, ey + 6, ex + 10, ey + 16], fill=(85, 199, 218))
    draw.text((ex + 22, ey), "KURAGE AI TRADING DIARY", font=eyebrow_font, fill=accent)

    draw.text((68, 175), "Kurage 暗号資産", font=title_font, fill=ink)
    draw.text((68, 255), "AI 自動取引日記", font=title_font, fill=ink)

    draw.text((70, 350), "AI VTuberのKurageちゃんが、", font=sub_font, fill=muted)
    draw.text((70, 392), "暗号資産の自動取引戦略と結果を報告します。", font=sub_font, fill=muted)

    draw.text((70, 560), "kurage.exbridge.jp/blog", font=sub_font, fill=accent)

    bg.convert("RGB").save(OUT_PATH, "PNG", optimize=True)
    print("saved:", OUT_PATH, bg.size)


if __name__ == "__main__":
    main()
