"""Convert arbitrary images into XteInk X3 sleep screens.

Pipeline (each step is a pure function, so it can be tested and reused):

1. ``load_gray``      decode, honour EXIF rotation, flatten alpha onto white, to 8-bit gray
2. ``trim_borders``   remove flat scanner margins / mats that touch the edges
3. ``smart_crop``     crop to 2:3 choosing the window with the most edge energy
4. resize to 528 x 792 (Lanczos), autocontrast, ``flatten_paper`` (paper tone -> white),
   light unsharp mask -> the *master* (pre-dither gray image)
5. ``quantize``       snap near-paper / near-ink tones, then Floyd-Steinberg to 4 levels
6. ``x3toolkit.bmp.encode_bmp``  2-bpp top-down BMP, palette 0/85/170/255

Requires Pillow.
"""
from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .bmp import FOUR_LEVELS, X3_HEIGHT, X3_WIDTH, encode_bmp

ASPECT = X3_WIDTH / X3_HEIGHT


def load_gray(path, max_short_side: int = 1300) -> Image.Image:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    g = im.convert("L")
    w, h = g.size
    if min(w, h) > max_short_side:
        s = max_short_side / min(w, h)
        g = g.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    return g


def _line_stats(vals: list[int]) -> tuple[float, float]:
    n = len(vals)
    m = sum(vals) / n
    return m, (sum((v - m) ** 2 for v in vals) / n) ** 0.5


def trim_borders(g: Image.Image, max_frac: float = 0.2, keep: float = 0.04) -> Image.Image:
    """Trim flat margins touching each edge (up to ``max_frac`` of the side).

    ``keep`` leaves a little of each trimmed margin (as a fraction of the side) so the
    subject does not end up touching the frame.
    """
    w, h = g.size
    s = 200 / min(w, h)
    sm = g.resize((max(2, round(w * s)), max(2, round(h * s))), Image.Resampling.BILINEAR)
    sw, sh = sm.size
    px = sm.load()

    def scan(get_line, n_lines: int) -> int:
        ref_m, _ = _line_stats(get_line(0))
        k = 0
        while k < int(n_lines * max_frac):
            m, sd = _line_stats(get_line(k))
            if not (sd <= 9 and abs(m - ref_m) <= 14):
                break
            k += 1
        return max(0, k - round(n_lines * keep)) if k else 0

    top = scan(lambda k: [px[x, k] for x in range(sw)], sh)
    bottom = scan(lambda k: [px[x, sh - 1 - k] for x in range(sw)], sh)
    left = scan(lambda k: [px[k, y] for y in range(sh)], sw)
    right = scan(lambda k: [px[sw - 1 - k, y] for y in range(sh)], sw)
    box = (round(left / s), round(top / s), w - round(right / s), h - round(bottom / s))
    if box[2] - box[0] < w * 0.5 or box[3] - box[1] < h * 0.5:
        return g
    return g.crop(box)


def smart_crop(g: Image.Image, aspect: float = ASPECT, steps: int = 24) -> Image.Image:
    """Crop to ``aspect``, sliding the window to maximise centre-weighted edge energy."""
    w, h = g.size
    if abs(w / h - aspect) < 0.005:
        return g
    horizontal = w / h > aspect
    cw, ch = (round(h * aspect), h) if horizontal else (w, round(w / aspect))
    span = (w - cw) if horizontal else (h - ch)
    s = 160 / min(w, h)
    edges = g.resize((max(2, round(w * s)), max(2, round(h * s))),
                     Image.Resampling.BILINEAR).filter(ImageFilter.FIND_EDGES)
    best, best_off = -1.0, span // 2
    for i in range(steps + 1):
        off = round(span * i / steps)
        box = ((off * s, 0, (off + cw) * s, edges.size[1]) if horizontal
               else (0, off * s, edges.size[0], (off + ch) * s))
        e = ImageStat.Stat(edges.crop(tuple(int(round(v)) for v in box))).mean[0]
        dist = abs(off - span / 2) / (span / 2) if span else 0
        score = e * (1 - 0.3 * dist)
        if score > best:
            best, best_off = score, off
    return (g.crop((best_off, 0, best_off + cw, ch)) if horizontal
            else g.crop((0, best_off, cw, best_off + ch)))


def paper_level(g: Image.Image) -> int | None:
    """Estimate the tone of blank paper: the most common level among the bright pixels.

    Returns None when no bright tone dominates (e.g. a photograph or a dark print).
    """
    hist = g.histogram()
    bright = hist[170:]
    peak = max(range(len(bright)), key=bright.__getitem__) + 170
    share = sum(hist[max(0, peak - 8):peak + 9]) / max(1, sum(hist))
    return peak if share >= 0.15 else None


def flatten_paper(g: Image.Image, margin: int = 10) -> Image.Image:
    """Map the paper tone (and anything lighter) to pure white, stretching the rest linearly.

    Old prints are scanned on cream or foxed paper. Left alone, that texture becomes
    a field of isolated dots after error diffusion; on an e-ink panel blank paper
    should simply be white.
    """
    p = paper_level(g)
    if p is None:
        return g
    knee = max(1, p - margin)
    return g.point([255 if v >= knee else round(v * 255 / knee) for v in range(256)])


def make_master(path, autocontrast: float = 0.5, paper: bool = True, sharpen: bool = True) -> Image.Image:
    """Source image -> 528 x 792 8-bit gray master (before dithering)."""
    g = smart_crop(trim_borders(load_gray(path)))
    g = g.resize((X3_WIDTH, X3_HEIGHT), Image.Resampling.LANCZOS)
    if autocontrast:
        g = ImageOps.autocontrast(g, cutoff=autocontrast)
    if paper:
        g = flatten_paper(g)
    if sharpen:  # restores line crispness lost in the downscale; dithering softens edges
        g = g.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=3))
    return g


def quantize(g: Image.Image, levels: tuple[int, ...] = FOUR_LEVELS, dither: bool = True,
             snap_dark: int = 12, snap_light: int = 16) -> bytes:
    """Reduce an 'L' image to ``levels``; returns one luminance byte per pixel.

    Tones within ``snap_dark`` of black or ``snap_light`` of white are snapped
    first, so error diffusion does not sprinkle isolated dots over blank paper.
    """
    if not dither:
        lut = [min(levels, key=lambda lv: abs(lv - v)) for v in range(256)]
        return bytes(lut[v] for v in g.tobytes())
    lo, hi = levels[0] + snap_dark, levels[-1] - snap_light
    g = g.point([levels[-1] if v >= hi else levels[0] if v <= lo else v for v in range(256)])
    pal = Image.new("P", (1, 1))
    flat = [c for lv in levels for c in (lv, lv, lv)]
    pal.putpalette(flat + flat[-3:] * (256 - len(levels)))
    q = g.convert("RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    lut = list(levels) + [levels[-1]] * (256 - len(levels))
    return bytes(lut[i] for i in q.tobytes())


def encode_x3(master: Image.Image, preset: str = "2bit") -> bytes:
    """Encode a 528 x 792 master with one of the two presets used on the device.

    * ``2bit``  2 bpp, palette 0/85/170/255, top-down, Floyd-Steinberg on the computer.
                Drawn directly by CrossPoint 1.5.0 and 2.0.0.
    * ``24bit`` 24 bpp gray, bottom-up, not pre-dithered (the format of the
                device's original wallpapers; the firmware dithers it).
    """
    if master.size != (X3_WIDTH, X3_HEIGHT) or master.mode != "L":
        raise ValueError("master must be a 528x792 'L' image")
    if preset == "2bit":
        return encode_bmp(quantize(master), X3_WIDTH, X3_HEIGHT, 2, FOUR_LEVELS, top_down=True)
    if preset == "24bit":
        return encode_bmp(master.tobytes(), X3_WIDTH, X3_HEIGHT, 24, top_down=False)
    raise ValueError(f"unknown preset {preset!r}")


def preview(levels_bytes: bytes) -> Image.Image:
    """Render quantised luminance bytes as a PNG-able image (what the panel shows)."""
    return Image.frombytes("L", (X3_WIDTH, X3_HEIGHT), levels_bytes)
