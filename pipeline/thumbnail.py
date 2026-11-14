"""1280x720 thumbnail with big, readable text over a darkened frame."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def _font(size):
    for p in FONTS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_thumbnail(frame_png, text, badge, out_jpg):
    img = Image.open(frame_png).convert("RGB")
    img = img.resize((1280, int(1280 * img.height / img.width))) if img.width != 1280 else img
    left = max((img.height - 720) // 2, 0)
    img = img.crop((0, left, 1280, left + 720))

    # darken left side for legibility
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for x in range(1280):
        d.line([(x, 0), (x, 720)], fill=(0, 0, 0, int(215 * max(0, 1 - x / 1000))))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    words, size = text.upper().split(), 150
    while size > 70:
        font = _font(size)
        lines, cur = [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= 760:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= 3 and all(draw.textlength(l, font=font) <= 760 for l in lines):
            break
        size -= 8
    y = (720 - len(lines) * size * 1.1) / 2
    for i, line in enumerate(lines):
        fill = (255, 221, 0) if i == len(lines) - 1 else (255, 255, 255)
        draw.text((60, y + i * size * 1.1), line, font=font, fill=fill, stroke_width=6, stroke_fill=(0, 0, 0))
    bf = _font(34)
    bw = draw.textlength(badge.upper(), font=bf)
    draw.rounded_rectangle([(40, 36), (40 + bw + 40, 96)], radius=12, fill=(220, 38, 38))
    draw.text((60, 44), badge.upper(), font=bf, fill="white")

    quality = 90
    while quality > 50:
        img.save(out_jpg, "JPEG", quality=quality)
        if Path(out_jpg).stat().st_size < 1_900_000:
            break
        quality -= 10
    return out_jpg
