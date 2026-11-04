#!/usr/bin/env python3
"""
Generates the Mat-Baker brand assets (SVG + PNG) from one geometry description, so the vector
and raster versions always match.

    python branding/build_branding.py

Needs: Pillow, numpy, fontTools and the Poppins font (SIL OFL 1.1). Set POPPINS_DIR if the font
lives somewhere else. Edit PALETTE below to recolour everything.

Outputs (written next to this script):
    mat-baker-icon.svg / -1024.png / -512.png / -256.png     square app icon (256 px = extensions.blender.org icon)
    mat-baker-logo-light.svg / .png                          horizontal logo for light backgrounds
    mat-baker-logo-dark.svg / .png                           horizontal logo for dark backgrounds
    github-social-preview-1280x640.png                       GitHub repository social preview
    extension-featured-image-1920x1080.png                   extensions.blender.org featured image (16:9)
    pbr-maps-illustration.png                                illustration of the four maps (README)
"""
import math
import os
import pathlib

import numpy as np
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent
FONT_DIR = pathlib.Path(os.environ.get("POPPINS_DIR", "/usr/share/fonts/truetype/google-fonts"))

PALETTE = {
    "slate": "#1B1F2A",       # icon tile / dark backgrounds
    "slate_2": "#252B3B",     # raised panels on dark backgrounds
    "clay": "#E2725B",        # base colour quadrant, accent
    "periwinkle": "#8E8CFB",  # normal-map quadrant
    "mint": "#5ED6B5",        # roughness quadrant
    "butter": "#F6D68C",      # height quadrant
    "paper": "#F4F6FA",       # text on dark
    "muted_dark": "#A9B1C3",  # secondary text on dark
}


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- fonts
class Face:
    """A TrueType face that can lay out text and give both SVG outlines and PIL fonts."""

    def __init__(self, filename):
        self.path = str(FONT_DIR / filename)
        self.tt = TTFont(self.path)
        self.upm = self.tt["head"].unitsPerEm
        self.cmap = self.tt.getBestCmap()
        self.hmtx = self.tt["hmtx"]
        self.glyphs = self.tt.getGlyphSet()
        self._pil = {}

    def advance(self, ch, size):
        return self.hmtx[self.cmap[ord(ch)]][0] * size / self.upm

    def width(self, text, size, tracking=0.0):
        return sum(self.advance(c, size) for c in text) + tracking * size * (len(text) - 1)

    def outline(self, ch):
        pen = SVGPathPen(self.glyphs)
        self.glyphs[self.cmap[ord(ch)]].draw(pen)
        return pen.getCommands()

    def pil(self, px):
        px = int(round(px))
        if px not in self._pil:
            self._pil[px] = ImageFont.truetype(self.path, px, layout_engine=ImageFont.Layout.BASIC)
        return self._pil[px]


BOLD, MEDIUM, REGULAR = Face("Poppins-Bold.ttf"), Face("Poppins-Medium.ttf"), Face("Poppins-Regular.ttf")


# --------------------------------------------------------------------------- scene
class Scene:
    """Flat vector scene that renders to SVG and (supersampled) PNG."""

    def __init__(self, w, h):
        self.w, self.h, self.items = w, h, []

    # primitives
    def rrect(self, x, y, w, h, r, fill, opacity=1.0):
        self.items.append(dict(kind="rrect", x=x, y=y, w=w, h=h, r=r, fill=fill, op=opacity))

    def circle(self, cx, cy, radius, fill, opacity=1.0):
        self.items.append(dict(kind="circle", cx=cx, cy=cy, R=radius, fill=fill, op=opacity))

    def pie(self, cx, cy, radius, a0, a1, fill, opacity=1.0):
        """Pie slice, angles in degrees, clockwise from 3 o'clock (screen coordinates)."""
        self.items.append(dict(kind="pie", cx=cx, cy=cy, R=radius, a0=a0, a1=a1, fill=fill, op=opacity))

    def text(self, face, string, x, baseline, size, fill, tracking=0.0, opacity=1.0):
        pen_x = x
        for ch in string:
            if ch != " ":
                self.items.append(dict(kind="glyph", face=face, ch=ch, x=pen_x, y=baseline, size=size, fill=fill, op=opacity))
            pen_x += face.advance(ch, size) + tracking * size
        return pen_x

    # SVG ---------------------------------------------------------------------------------------
    def svg(self, title):
        f = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {f(self.w)} {f(self.h)}" '
               f'width="{f(self.w)}" height="{f(self.h)}" role="img" aria-label="{title}">',
               f"  <title>{title}</title>"]
        for it in self.items:
            op = f' fill-opacity="{it["op"]}"' if it["op"] < 1 else ""
            if it["kind"] == "rrect":
                out.append(f'  <rect x="{f(it["x"])}" y="{f(it["y"])}" width="{f(it["w"])}" height="{f(it["h"])}" '
                           f'rx="{f(it["r"])}" fill="{it["fill"]}"{op}/>')
            elif it["kind"] == "circle":
                out.append(f'  <circle cx="{f(it["cx"])}" cy="{f(it["cy"])}" r="{f(it["R"])}" fill="{it["fill"]}"{op}/>')
            elif it["kind"] == "pie":
                cx, cy, R = it["cx"], it["cy"], it["R"]
                p0 = (cx + R * math.cos(math.radians(it["a0"])), cy + R * math.sin(math.radians(it["a0"])))
                p1 = (cx + R * math.cos(math.radians(it["a1"])), cy + R * math.sin(math.radians(it["a1"])))
                large = 1 if (it["a1"] - it["a0"]) % 360 > 180 else 0
                out.append(f'  <path d="M{f(cx)} {f(cy)}L{f(p0[0])} {f(p0[1])}A{f(R)} {f(R)} 0 {large} 1 {f(p1[0])} {f(p1[1])}Z" '
                           f'fill="{it["fill"]}"{op}/>')
            else:
                face, s = it["face"], it["size"] / it["face"].upm
                out.append(f'  <path transform="translate({f(it["x"])} {f(it["y"])}) scale({s:.5f} {-s:.5f})" '
                           f'd="{face.outline(it["ch"])}" fill="{it["fill"]}"{op}/>')
        out.append("</svg>")
        return "\n".join(out) + "\n"

    # PNG ---------------------------------------------------------------------------------------
    def render(self, out_w=None, supersample=4):
        out_w = out_w or self.w
        k = out_w / self.w
        S = k * supersample
        W, H = int(round(self.w * S)), int(round(self.h * S))
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for it in self.items:
            layer = canvas if it["op"] >= 1 else Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            color = rgb(it["fill"]) + (255,)
            if it["kind"] == "rrect":
                d.rounded_rectangle([it["x"] * S, it["y"] * S, (it["x"] + it["w"]) * S - 1, (it["y"] + it["h"]) * S - 1],
                                    radius=it["r"] * S, fill=color)
            elif it["kind"] == "circle":
                cx, cy, R = it["cx"] * S, it["cy"] * S, it["R"] * S
                d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=color)
            elif it["kind"] == "pie":
                cx, cy, R = it["cx"] * S, it["cy"] * S, it["R"] * S
                d.pieslice([cx - R, cy - R, cx + R, cy + R], it["a0"], it["a1"], fill=color)
            else:
                font = it["face"].pil(it["size"] * S)
                d.text((it["x"] * S, it["y"] * S), it["ch"], font=font, fill=color, anchor="ls")
            if layer is not canvas:
                a = layer.getchannel("A").point(lambda v, o=it["op"]: int(v * o))
                layer.putalpha(a)
                canvas = Image.alpha_composite(canvas, layer)
        return canvas.resize((int(round(self.w * k)), int(round(self.h * k))), Image.LANCZOS)


# --------------------------------------------------------------------------- the mark
TILES = [  # (colour key, column, row)  the four maps: base colour, normal, height, roughness
    ("clay", 0, 0),
    ("periwinkle", 1, 0),
    ("butter", 0, 1),
    ("mint", 1, 1),
]


def add_mark(scene, x, y, size, with_tile):
    """
    The Mat-Baker mark: four rounded "map" tiles (the texture pack) with a flat two-tone material ball
    in front of them. With with_tile=False the ball's cut-out ring uses the slate colour, so only use
    that variant on slate backgrounds.
    """
    k = size / 512.0
    if with_tile:
        scene.rrect(x, y, size, size, 116 * k, PALETTE["slate"])
    tile, gap, margin, radius = 196 * k, 24 * k, 48 * k, 46 * k
    for key, col, row in TILES:
        scene.rrect(x + margin + col * (tile + gap), y + margin + row * (tile + gap), tile, tile, radius, PALETTE[key])
    cx, cy, ball = x + size / 2, y + size / 2, 96 * k
    scene.circle(cx, cy, ball + 18 * k, PALETTE["slate"])                    # cut-out ring
    scene.circle(cx, cy, ball, PALETTE["paper"])                             # lit side
    scene.pie(cx, cy, ball, 315, 135, PALETTE["slate"], opacity=0.16)             # shaded half


def icon_scene():
    s = Scene(512, 512)
    add_mark(s, 0, 0, 512, with_tile=True)
    return s


def lockup_scene(text_color, size=512):
    """Horizontal logo: tile icon + 'Mat-Baker' wordmark, transparent background."""
    font_size = 214
    tracking = 0.0
    text_w = BOLD.width("Mat-Baker", font_size, tracking)
    gap = 60
    s = Scene(size + gap + math.ceil(text_w), size)
    add_mark(s, 0, 0, size, with_tile=True)
    cap_h = 0.70 * font_size                      # Poppins capital height is about 0.70 em
    baseline = size / 2 + cap_h / 2
    x = size + gap
    x = s.text(BOLD, "Mat-", x, baseline, font_size, text_color, tracking)
    s.text(BOLD, "Baker", x + tracking * font_size, baseline, font_size, PALETTE["clay"], tracking)
    return s


# --------------------------------------------------------------------------- procedural brick maps (illustration)
def _blur_wrap(a, sigma):
    h, w = a.shape
    fy, fx = np.fft.fftfreq(h)[:, None], np.fft.rfftfreq(w)[None, :]
    return np.fft.irfft2(np.fft.rfft2(a) * np.exp(-2 * (math.pi * sigma) ** 2 * (fx * fx + fy * fy)), s=a.shape)


def _smooth(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def brick_maps(n=512, rows=7, cols=3.5):
    """A procedural brick wall and the four maps derived from it (base colour, normal, roughness, height)."""
    rng = np.random.default_rng(11)
    yy, xx = (np.mgrid[0:n, 0:n].astype(np.float32) / n)
    ry = yy * rows
    row = np.floor(ry).astype(int)
    rx = xx * cols + (row % 2) * 0.5
    col = np.floor(rx).astype(int)
    fx, fy = rx - col, ry - row
    wpx, hpx = n / cols, n / rows
    dist = np.minimum(np.minimum(fx, 1 - fx) * wpx, np.minimum(fy, 1 - fy) * hpx)   # px to nearest brick edge

    low = _blur_wrap(rng.standard_normal((n, n)), 18)
    low = (low - low.min()) / (low.max() - low.min())
    fine = _blur_wrap(rng.standard_normal((n, n)), 1.4)
    fine = (fine - fine.min()) / (fine.max() - fine.min())

    brick = _smooth(dist, 5, 7.5)                      # 0 = mortar, 1 = brick (anti-aliased)
    bevel = _smooth(dist, 5, 22)                       # soft rounded edge
    height = 0.12 + 0.88 * bevel * (0.88 + 0.12 * low) - 0.05 * fine * brick
    height = np.clip(height, 0, 1).astype(np.float32)

    tones = np.array([[0.74, 0.31, 0.21], [0.80, 0.38, 0.27], [0.66, 0.27, 0.19], [0.84, 0.46, 0.33]], np.float32)
    ids = rng.integers(0, 4, (int(rows) + 2, int(np.ceil(cols)) + 3))
    tone = tones[ids[row % ids.shape[0], col % ids.shape[1]]]
    shade = (0.86 + 0.20 * low + 0.10 * fine)[..., None]
    mortar = np.array([0.74, 0.72, 0.68], np.float32) * (0.9 + 0.15 * fine)[..., None]
    base = mortar * (1 - brick[..., None]) + np.clip(tone * shade, 0, 1) * brick[..., None]

    rough = np.clip(0.97 * (1 - brick) + (0.62 + 0.22 * fine + 0.1 * low) * brick, 0, 1)

    gy, gx = np.gradient(height * (n * 0.045))
    nz = np.ones_like(gx)
    inv = 1.0 / np.sqrt(gx * gx + gy * gy + nz * nz)
    normal = np.stack([-gx * inv, gy * inv, nz * inv], -1) * 0.5 + 0.5      # OpenGL (+Y)

    u8 = lambda a: np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    gray = lambda a: np.repeat(u8(a)[..., None], 3, -1)
    return {"Base Color": u8(base), "Normal": u8(normal), "Roughness": gray(rough), "Height": gray(height)}


def rounded_tile(arr, size, radius):
    """Resize an RGB array to size x size with anti-aliased rounded corners."""
    img = Image.fromarray(arr).resize((size, size), Image.LANCZOS).convert("RGBA")
    S = 4
    mask = Image.new("L", (size * S, size * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size * S - 1, size * S - 1], radius=radius * S, fill=255)
    img.putalpha(mask.resize((size, size), Image.LANCZOS))
    return img


def paste_tiles(base_img, x, y, size, gap, radius, tiles):
    for i, name in enumerate(tiles):
        base_img.alpha_composite(rounded_tile(tiles[name], size, radius), (int(x + i * (size + gap)), int(y)))


# --------------------------------------------------------------------------- compositions
def banner(w, h, mark_size, word_size, tag_size, tile_size, layout):
    tiles = brick_maps()
    bg = Scene(w, h)
    bg.rrect(0, 0, w, h, 0, PALETTE["slate"])
    img = bg.render()
    fg = Scene(w, h)

    if layout == "wide":                                   # 1920 x 1080 featured image
        left = 140
        add_mark(fg, left, 110, mark_size, with_tile=False)
        base_y = 110 + mark_size / 2 + 0.35 * word_size
        x = left + mark_size + 56
        x = fg.text(BOLD, "Mat-", x, base_y, word_size, PALETTE["paper"])
        fg.text(BOLD, "Baker", x, base_y, word_size, PALETTE["clay"])
        fg.text(MEDIUM, "Bake procedural materials into PBR texture packs", left, 110 + mark_size + 86, tag_size, PALETTE["muted_dark"])
        gap = (w - 2 * left - 4 * tile_size) // 3
        ty = 500
        paste_tiles(img, left, ty, tile_size, gap, 28, tiles)
        for i, name in enumerate(tiles):
            fg.text(MEDIUM, name, left + i * (tile_size + gap), ty + tile_size + 56, 34, PALETTE["paper"])
        fg.text(REGULAR, "Presets for Unreal, Unity, glTF and Godot   ·   Free and open source (GPL-3.0-or-later)",
                left, h - 64, 30, PALETTE["muted_dark"])
    else:                                                  # 1280 x 640 GitHub social preview (mark stacked above the wordmark)
        left = 84
        gap = 24
        gx, gy = w - 84 - (2 * tile_size + gap), (h - (2 * tile_size + gap)) // 2
        add_mark(fg, left, 84, mark_size, with_tile=False)
        base_y = 84 + mark_size + 40 + 0.70 * word_size
        x = fg.text(BOLD, "Mat-", left, base_y, word_size, PALETTE["paper"])
        end_x = fg.text(BOLD, "Baker", x, base_y, word_size, PALETTE["clay"])
        assert end_x < gx - 32, f"wordmark ({end_x:.0f}px) would touch the tiles at {gx}px"
        fg.text(MEDIUM, "Bake procedural materials", left, base_y + tag_size * 1.9, tag_size, PALETTE["muted_dark"])
        fg.text(MEDIUM, "into PBR texture packs", left, base_y + tag_size * 3.3, tag_size, PALETTE["muted_dark"])
        fg.text(REGULAR, "Blender 4.2+   ·   GPL-3.0-or-later", left, h - 58, 26, PALETTE["muted_dark"])
        for i, name in enumerate(tiles):
            r, c = divmod(i, 2)
            img.alpha_composite(rounded_tile(tiles[name], tile_size, 22), (gx + c * (tile_size + gap), gy + r * (tile_size + gap)))
    img.alpha_composite(fg.render())
    return img


def illustration():
    size, gap, pad = 340, 40, 60
    w, h = 2 * pad + 4 * size + 3 * gap, 520
    tiles = brick_maps()
    bg = Scene(w, h)
    bg.rrect(0, 0, w, h, 36, PALETTE["slate"])
    img = bg.render()
    paste_tiles(img, pad, pad, size, gap, 24, tiles)
    fg = Scene(w, h)
    for i, name in enumerate(tiles):
        fg.text(MEDIUM, name, pad + i * (size + gap), pad + size + 56, 32, PALETTE["paper"])
    img.alpha_composite(fg.render())
    return img


def main():
    icon = icon_scene()
    (OUT / "mat-baker-icon.svg").write_text(icon.svg("Mat-Baker icon"), encoding="utf-8")
    for px in (1024, 512, 256):
        icon.render(px).save(OUT / f"mat-baker-icon-{px}.png")

    for name, color in (("light", "#1B1F2A"), ("dark", PALETTE["paper"])):
        sc = lockup_scene(color)
        (OUT / f"mat-baker-logo-{name}.svg").write_text(sc.svg("Mat-Baker"), encoding="utf-8")
        sc.render(1600).save(OUT / f"mat-baker-logo-{name}.png")

    banner(1280, 640, 118, 98, 36, 230, "social").convert("RGB").save(OUT / "github-social-preview-1280x640.png")
    banner(1920, 1080, 190, 168, 52, 396, "wide").convert("RGB").save(OUT / "extension-featured-image-1920x1080.png")
    illustration().save(OUT / "pbr-maps-illustration.png")
    for f in sorted(OUT.glob("*")):
        if f.suffix in (".svg", ".png"):
            print(f"{f.name:46s}{f.stat().st_size / 1024:8.1f} KB")


if __name__ == "__main__":
    main()
