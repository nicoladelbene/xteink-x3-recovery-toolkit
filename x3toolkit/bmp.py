"""BMP encoding and validation for XteInk X3 sleep screens (528 x 792).

Encodings and firmware behaviour were taken from reading CrossPoint's source at
tags ``v1.5.0`` and ``2.0.0`` (``lib/GfxRenderer/Bitmap.cpp`` and
``src/activities/boot_sleep/SleepActivity.cpp``). Summary:

* Supported: 1/2/4/8/24/32 bpp, BI_RGB (BI_BITFIELDS only at 32 bpp),
  top-down or bottom-up, up to 2048 x 3072.
* A palette entry's display level is ``((77R + 150G + 29B) >> 8) >> 6``,
  so palette 0/85/170/255 maps exactly to the panel's four levels 0..3.
* Images of <= 2 bpp are drawn with their palette as-is (no dithering).
* CrossPoint 2.0.0 draws a sleep image directly only if it is exactly the
  screen size *and* <= 2 bpp; anything else is scaled/dithered once and cached
  under ``/.crosspoint/sleep_<hash>.bmp``.

This module is pure Python (no Pillow) so it can validate files anywhere.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

X3_WIDTH, X3_HEIGHT = 528, 792
FOUR_LEVELS = (0, 85, 170, 255)
SUPPORTED_BPP = (1, 2, 4, 8, 24, 32)


def row_stride(width: int, bpp: int) -> int:
    return (width * bpp + 31) // 32 * 4


def encode_bmp(gray: bytes, width: int, height: int, bpp: int,
               levels: tuple[int, ...] = FOUR_LEVELS, top_down: bool = True) -> bytes:
    """Encode luminance bytes (top-down, one byte per pixel) as an uncompressed BMP.

    For bpp <= 8 every pixel value must be one of ``levels`` (palette index i =
    levels[i]); packing is MSB-first, as the firmware reads it. For bpp 24 any
    value is allowed and written as B = G = R.
    """
    if bpp not in (1, 2, 4, 8, 24):
        raise ValueError(f"unsupported bpp {bpp}")
    if len(gray) != width * height:
        raise ValueError("pixel buffer size does not match width x height")
    stride = row_stride(width, bpp)
    palette = b""
    lut = None
    if bpp <= 8:
        if len(levels) > (1 << bpp):
            raise ValueError(f"{len(levels)} levels do not fit in {bpp} bpp")
        palette = b"".join(struct.pack("<BBBB", v, v, v, 0) for v in levels)
        lut = {v: i for i, v in enumerate(levels)}
    rows = []
    order = range(height) if top_down else range(height - 1, -1, -1)
    for y in order:
        src = gray[y * width:(y + 1) * width]
        row = bytearray(stride)
        if bpp == 24:
            for x, v in enumerate(src):
                row[3 * x] = row[3 * x + 1] = row[3 * x + 2] = v
        else:
            ppb = 8 // bpp
            for x, v in enumerate(src):
                try:
                    i = lut[v]
                except KeyError:
                    raise ValueError(f"pixel value {v} is not one of the palette levels {levels}") from None
                if bpp == 8:
                    row[x] = i
                else:
                    row[x // ppb] |= i << (8 - bpp * (x % ppb + 1))
        rows.append(bytes(row))
    pixels = b"".join(rows)
    off = 14 + 40 + len(palette)
    n_pal = len(levels) if bpp <= 8 else 0
    return (struct.pack("<2sIHHI", b"BM", off + len(pixels), 0, 0, off)
            + struct.pack("<IiiHHIIiiII", 40, width, -height if top_down else height, 1, bpp, 0,
                          len(pixels), 2835, 2835, n_pal, n_pal)
            + palette + pixels)


@dataclass
class BmpInfo:
    width: int
    height: int          # always positive
    top_down: bool
    bpp: int
    compression: int
    off_bits: int
    palette: list[tuple[int, int, int]] = field(default_factory=list)


def parse_header(data: bytes) -> BmpInfo:
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("not a BMP file")
    off = struct.unpack_from("<I", data, 10)[0]
    bi_size, w, h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", data, 14)
    clr_used = struct.unpack_from("<I", data, 46)[0]
    if bi_size < 40:
        raise ValueError(f"unsupported info header size {bi_size}")
    n_pal = clr_used or ((1 << bpp) if bpp <= 8 else 0)
    pal = []
    for i in range(n_pal):
        o = 14 + bi_size + 4 * i
        if o + 4 > len(data):
            break
        b, g, r, _ = struct.unpack_from("<BBBB", data, o)
        pal.append((r, g, b))
    return BmpInfo(w, abs(h), h < 0, bpp, comp, off, pal)


def display_level(rgb: tuple[int, int, int]) -> int:
    """Panel gray level (0 = black .. 3 = white) the firmware uses for a palette entry."""
    r, g, b = rgb
    return ((77 * r + 150 * g + 29 * b) >> 8) >> 6


@dataclass
class Validation:
    ok: bool
    errors: list[str]
    warnings: list[str]
    direct_on_2_0: bool   # drawn without conversion by CrossPoint 2.0.0
    info: BmpInfo | None


def validate_sleep_bmp(data: bytes, width: int = X3_WIDTH, height: int = X3_HEIGHT) -> Validation:
    """Check a sleep-screen BMP against the CrossPoint parser rules and the X3 screen."""
    errors, warnings = [], []
    try:
        info = parse_header(data)
    except ValueError as e:
        return Validation(False, [str(e)], [], False, None)
    if info.bpp not in SUPPORTED_BPP:
        errors.append(f"bit depth {info.bpp} not supported by the firmware")
    if not (info.compression == 0 or (info.compression == 3 and info.bpp == 32)):
        errors.append(f"compression {info.compression} not supported (BI_RGB only)")
    if info.width > 2048 or info.height > 3072:
        errors.append("larger than the firmware's 2048 x 3072 limit")
    if info.bpp <= 8:
        expected = info.off_bits + row_stride(info.width, info.bpp) * info.height
        if len(data) < expected:
            errors.append(f"file truncated: {len(data)} < {expected} bytes")
    elif len(data) < info.off_bits + row_stride(info.width, info.bpp) * info.height:
        errors.append("file truncated")
    if (info.width, info.height) != (width, height):
        warnings.append(f"{info.width}x{info.height} is not the screen size {width}x{height}; "
                        "the firmware will scale or centre it")
    if info.bpp <= 2 and info.palette:
        lv = [display_level(p) for p in info.palette]
        if len(set(lv)) < len(lv):
            warnings.append(f"palette entries collapse onto the same panel level: {lv}")
    direct = not errors and (info.width, info.height) == (width, height) and info.bpp <= 2
    if not errors and not direct:
        warnings.append("CrossPoint 2.0.0 will convert and cache this image on first use")
    return Validation(not errors, errors, warnings, direct, info)


def decode_levels(data: bytes) -> list[int]:
    """Decode a <= 8 bpp BMP to per-pixel panel levels (top-down), as the firmware would."""
    info = parse_header(data)
    if info.bpp > 8:
        raise ValueError("decode_levels handles indexed BMPs only")
    lv = [display_level(p) for p in info.palette]
    stride = row_stride(info.width, info.bpp)
    ppb = 8 // info.bpp
    mask = (1 << info.bpp) - 1
    out = []
    for y in range(info.height):
        src_y = y if info.top_down else info.height - 1 - y
        row = data[info.off_bits + src_y * stride: info.off_bits + (src_y + 1) * stride]
        for x in range(info.width):
            if info.bpp == 8:
                idx = row[x]
            else:
                idx = (row[x // ppb] >> (8 - info.bpp * (x % ppb + 1))) & mask
            out.append(lv[idx])
    return out
