"""Generate app.ico for the build.

Drawn at 1024px and downsampled per icon size, so the small sizes stay crisp
instead of being a blurry resize of one bitmap. Motif: a vertical film strip
(the footage is 9:16) with the middle frame lifted out in amber -- the one
screenshot pulled from each shot.
"""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
BG_TOP, BG_BOT = (79, 70, 229), (147, 51, 234)      # indigo -> violet
STRIP = (248, 250, 252)
FRAME = (203, 213, 225)
ACCENT = (245, 158, 11)                              # amber, the picked frame


def rounded(size, radius, fill):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                          radius=radius, fill=fill)
    return img


def gradient(size, top, bot):
    img = Image.new("RGB", (1, size[1]))
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        img.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bot)))
    return img.resize(size, Image.BICUBIC)


def build():
    card = gradient((S, S), BG_TOP, BG_BOT).convert("RGBA")
    mask = rounded((S, S), int(S * 0.22), (255, 255, 255, 255)).split()[3]
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(card, (0, 0), mask)

    # soft top-left sheen so the tile does not read as flat colour
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse([-S * 0.55, -S * 0.75, S * 0.78, S * 0.30],
                                  fill=(255, 255, 255, 38))
    sheen = sheen.filter(ImageFilter.GaussianBlur(S * 0.09))   # no hard rim
    base.alpha_composite(Image.composite(sheen, Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                                         mask))

    d = ImageDraw.Draw(base)

    # --- film strip ------------------------------------------------------
    sw, sh = int(S * 0.46), int(S * 0.72)
    sx, sy = (S - sw) // 2, (S - sh) // 2
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([sx, sy + 14, sx + sw, sy + sh + 14],
                                             radius=int(S * 0.035), fill=(30, 20, 70, 110))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.022)))
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=int(S * 0.035), fill=STRIP)

    # sprocket holes down both edges
    hw, hh = int(sw * 0.105), int(sh * 0.052)
    gap = (sh - 5 * hh) / 6
    for i in range(5):
        y = sy + gap + i * (hh + gap)
        for x in (sx + int(sw * 0.055), sx + sw - int(sw * 0.055) - hw):
            d.rounded_rectangle([x, y, x + hw, y + hh], radius=hh // 3,
                                fill=(120, 113, 200))

    # three frames; the middle one is the extracted still
    fx0, fx1 = sx + int(sw * 0.235), sx + sw - int(sw * 0.235)
    fh = int(sh * 0.245)
    fgap = (sh - 3 * fh) / 4
    tops = [sy + fgap + i * (fh + fgap) for i in range(3)]
    for t in (tops[0], tops[2]):
        d.rounded_rectangle([fx0, t, fx1, t + fh], radius=int(S * 0.016), fill=FRAME)

    t = tops[1]
    pop = int(S * 0.035)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        [fx0 - pop, t - pop * 0.5, fx1 + pop, t + fh + pop * 0.5],
        radius=int(S * 0.022), fill=(120, 60, 10, 130))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.018)))
    d.rounded_rectangle([fx0 - pop, t - pop * 0.5, fx1 + pop, t + fh + pop * 0.5],
                        radius=int(S * 0.022), fill=ACCENT)

    out = os.path.join(HERE, "app.ico")
    sizes = [256, 128, 64, 48, 32, 16]
    base.resize((256, 256), Image.LANCZOS).save(
        out, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=[base.resize((s, s), Image.LANCZOS) for s in sizes])
    base.resize((512, 512), Image.LANCZOS).save(os.path.join(HERE, "app_preview.png"))
    print(out)


if __name__ == "__main__":
    build()
