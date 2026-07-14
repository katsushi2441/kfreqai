"""ブログ記事のOGP画像ジェネレータ — タイトルから1200x630のシェア画像を生成する。

kurageブログテーマ(head.php)は $page->coverImage() があればog:imageに使う実装
だったが、投稿側がcoverImageを渡していなかったため全記事がデフォルト画像に
なっていた(2026-07-14ユーザー指摘)。生成した画像はFTPで
blog/bl-content/uploads/ へ置き、投稿APIに coverImage=<filename> を渡す。

外部アセット不要(PIL + Noto Sans CJK)。海色パレットはランディング/テーマと共通。
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

SEA_TOP = (10, 42, 64)       # 深海
SEA_BOTTOM = (30, 120, 150)  # 浅瀬のティール
ACCENT = (85, 199, 218)      # --sea
FOAM = (203, 238, 244)       # --line
INK = (255, 255, 255)


def _font(path, size):
    return ImageFont.truetype(path, size, index=0)


def _lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _bg():
    im = Image.new("RGB", (1, H), SEA_TOP)
    px = im.load()
    for y in range(H):
        px[0, y] = _lerp(SEA_TOP, SEA_BOTTOM, y / H)
    return im.resize((W, H)).convert("RGBA")


def _draw_bubbles(im):
    import random
    rnd = random.Random(11)  # 固定シード(記事間で見た目を揃える)
    for _ in range(26):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.choice([3, 4, 6, 9, 14])
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            [x - r, y - r, x + r, y + r],
            outline=ACCENT + (70,), width=2)
        im.alpha_composite(layer)


def _fit_lines(draw, text, font_path, max_width, start_size, min_size, max_lines):
    size = start_size
    while size >= min_size:
        font = _font(font_path, size)
        lines, cur = [], ""
        for ch in text:
            trial = cur + ch
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    break
        else:
            lines.append(cur)
            return lines, font
        size -= 4
    # 最小サイズでも収まらない場合は切り詰め
    font = _font(font_path, min_size)
    lines = lines[:max_lines]
    lines[-1] = lines[-1][:-1] + "…"
    return lines, font


def generate(title: str) -> bytes:
    im = _bg()
    _draw_bubbles(im)
    draw = ImageDraw.Draw(im)

    margin = 80
    # 上部バッジ(Noto CJKに絵文字グリフは無いので、クラゲは図形で描く)
    badge_font = _font(FONT_BOLD, 26)
    badge_text = "Kurage 暗号資産 AI 自動取引日記"
    jelly_w = 40
    text_w = draw.textlength(badge_text, font=badge_font)
    draw.rounded_rectangle([margin, 64, margin + jelly_w + text_w + 52, 116],
                           radius=26, fill=(255, 255, 255, 28), outline=ACCENT + (160,), width=2)
    # クラゲ: 傘(半円)+触手3本
    jx, jy = margin + 24, 78
    draw.pieslice([jx, jy, jx + 26, jy + 26], 180, 360, fill=ACCENT + (230,))
    for i, dx in enumerate((4, 12, 20)):
        draw.arc([jx + dx - 2, jy + 12, jx + dx + 4, jy + 30], 20, 200, fill=ACCENT + (200,), width=2)
    draw.text((margin + jelly_w + 22, 74), badge_text, font=badge_font, fill=FOAM)

    # タイトル(最大3行、幅に合わせて縮小)
    lines, font = _fit_lines(draw, title, FONT_BOLD, W - margin * 2, 62, 38, 3)
    y = 190
    for line in lines:
        draw.text((margin, y), line, font=font, fill=INK)
        y += int(font.size * 1.42)

    # 下部フッター
    foot_font = _font(FONT_REGULAR, 24)
    draw.line([margin, H - 110, W - margin, H - 110], fill=ACCENT + (120,), width=2)
    draw.text((margin, H - 88), "kurage.exbridge.jp/blog — 負けも直した過程も全部公開",
              font=foot_font, fill=FOAM)
    draw.text((W - margin - draw.textlength("dry-run / paper trading", font=foot_font), H - 88),
              "dry-run / paper trading", font=foot_font, fill=ACCENT)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    png = generate("テスト: モデルは「92%勝てる」と言い、実際は42%だった — 較正測定から正則化採用までの一日")
    open("/tmp/ogp_test.png", "wb").write(png)
    print(f"wrote /tmp/ogp_test.png ({len(png):,} bytes)")
