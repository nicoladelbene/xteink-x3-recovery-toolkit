"""Regenerate every chart and image example in assets/ from docs/data/*.csv.

    python tools/make_figures.py                       # charts only
    python tools/make_figures.py --examples SRC_DIR    # + wallpaper examples from CC0 sources

Charts are written twice, *-light.svg and *-dark.svg, and the README picks one
with <picture>. Colours: a validated categorical palette (slots 1-3 pass the
all-pairs colour-vision checks in both modes), recessive grid, direct labels.
Requires matplotlib (and Pillow for --examples).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
OUT = os.path.join(ROOT, "assets", "charts")
sys.path.insert(0, ROOT)

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", s1="#2a78d6", s2="#eb6834", s3="#1baf7a", crit="#d03b3b",
                  neutral="#c3c2b7", on_fill="#ffffff"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", s1="#3987e5", s2="#d95926", s3="#199e70", crit="#d03b3b",
                 neutral="#5f5e5a", on_fill="#ffffff"),
}


def rows(name):
    with open(os.path.join(DATA, name), newline="") as f:
        return list(csv.DictReader(f))


def setup(t, w=8.0, h=3.6):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "path"})
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(t["surface"])
    ax.set_facecolor(t["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["ink2"], length=0)
    ax.grid(axis="x", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def title(fig, t, text, sub):
    fig.text(0.012, 0.965, text, ha="left", va="top", fontsize=12.5, weight="bold", color=t["ink"])
    fig.text(0.012, 0.885, sub, ha="left", va="top", fontsize=9.5, color=t["ink2"])


def save(fig, name, mode):
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, f"{name}-{mode}.svg"), facecolor=fig.get_facecolor(),
                metadata={"Date": None, "Creator": "x3toolkit tools/make_figures.py"})
    plt.close(fig)


def chart_stages(mode):
    t = THEMES[mode]
    data = rows("epub-integrity-by-stage.csv")
    cats = [("verified_intact", "intact", t["s1"]), ("restored_clean_copy", "clean copy", t["s2"]),
            ("reconstructed_multiread", "multi-read rebuild", t["s3"]),
            ("still_damaged", "still damaged", t["crit"]), ("not_an_epub", "not an EPUB", t["neutral"])]
    fig, ax = setup(t, 8.4, 3.3)
    fig.subplots_adjust(left=0.27, right=0.98, top=0.66, bottom=0.14)
    y = list(range(len(data)))[::-1]
    for yi, r in zip(y, data):
        left = 0
        for key, label, color in cats:
            v = int(r[key])
            if not v:
                continue
            ax.barh(yi, v - 0.12, left=left + 0.06, height=0.56, color=color, linewidth=0)
            if v >= 3:
                ax.text(left + v / 2, yi, str(v), ha="center", va="center", fontsize=9.5,
                        color=t["on_fill"], weight="bold")
            left += v
    ax.set_yticks(y, [r["stage"] for r in data], color=t["ink"])
    ax.set_xlim(0, 44)
    ax.set_xticks([0, 11, 22, 33, 44])
    ax.set_xlabel("EPUB entries on the card (44)", color=t["ink2"])
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in cats]
    ax.legend(handles, [l for _, l, _ in cats], ncol=5, frameon=False, loc="lower left",
              bbox_to_anchor=(-0.36, 1.0), fontsize=9, labelcolor=t["ink2"], handlelength=1.0,
              columnspacing=1.2)
    title(fig, t, "EPUB integrity at each stage",
          "ZIP CRC-32 check of every book. Voting recovered none; clean copies and CRC-guided rebuilds recovered 11.")
    save(fig, "epub-integrity-by-stage", mode)


def chart_reads(mode):
    t = THEMES[mode]
    data = rows("per-read-error.csv")
    fig, ax = setup(t, 8.4, 3.6)
    fig.subplots_adjust(left=0.15, right=0.95, top=0.74, bottom=0.14)
    y = list(range(len(data)))[::-1]
    for yi, r in zip(y, data):
        v = int(r["wrong_bytes_vs_clean_copies"])
        vote = r["read"].startswith("Majority")
        ax.barh(yi, v, height=0.62, color=t["s2"] if vote else t["s1"], linewidth=0)
        ax.text(v + 80, yi, f"{v:,}", va="center", fontsize=9, color=t["ink"])
    ax.set_yticks(y, [r["read"] for r in data], color=t["ink"])
    ax.set_xlim(0, 6800)
    ax.set_xlabel("bytes that differ from independent clean copies (6 books)", color=t["ink2"])
    title(fig, t, "Every read of the failing card was wrong in a different way",
          "No read reported an I/O error. Read 1 was the most accurate; voting across reads made it worse, not better.")
    save(fig, "wrong-bytes-per-read", mode)


def chart_outcomes(mode):
    t = THEMES[mode]
    data = rows("multiread-reconstruction.csv")
    order = [("repaired", "fully rebuilt", t["s3"]), ("partial", "partly rebuilt", t["s2"]),
             ("unrepaired", "not rebuilt", t["s1"])]
    fig, ax = setup(t, 8.4, 3.2)
    fig.subplots_adjust(left=0.2, right=0.97, top=0.74, bottom=0.17)
    ax.set_xscale("log")
    ticks = []
    for yi, (key, label, color) in zip([2, 1, 0], order):
        xs = sorted(int(r["unstable_sectors"]) for r in data if r["result"] == key)
        seen: dict[int, int] = {}
        for x in xs:  # stack books with the same value instead of hiding them
            k = sum(1 for v in seen if abs(v - x) / x < 0.06)
            seen[x] = seen.get(x, 0) + 1
            ax.scatter([x], [yi + 0.22 * k], s=70, color=color, edgecolors=t["surface"],
                       linewidths=2, zorder=3)
        ticks.append(f"{label} ({len(xs)})")
    ax.set_yticks([2, 1, 0], ticks, color=t["ink"])
    ax.set_ylim(-0.5, 2.75)
    ax.set_xlim(1.6, 250)
    ax.set_xticks([2, 5, 10, 20, 50, 100, 200], ["2", "5", "10", "20", "50", "100", "200"])
    ax.set_xlabel("sectors of the book that read differently across 7 reads (log scale)", color=t["ink2"])
    title(fig, t, "Reconstruction works when damage is sparse",
          "17 damaged books. Every full rebuild had <= 24 unstable sectors; 7 of the 9 failures had more.")
    save(fig, "reconstruction-outcomes", mode)


def chart_wallpapers(mode):
    t = THEMES[mode]
    data = rows("wallpapers-by-source.csv")
    fig, ax = setup(t, 8.4, 3.4)
    fig.subplots_adjust(left=0.30, right=0.95, top=0.74, bottom=0.14)
    y = list(range(len(data)))[::-1]
    for yi, r in zip(y, data):
        v = int(r["images"])
        ax.barh(yi, v, height=0.62, color=t["s1"], linewidth=0)
        ax.text(v + 3, yi, str(v), va="center", fontsize=9, color=t["ink"])
    ax.set_yticks(y, [r["source"] for r in data], color=t["ink"])
    ax.set_xlim(0, 250)
    ax.set_xlabel("wallpapers installed (811 new, plus the 11 originals)", color=t["ink2"])
    title(fig, t, "Where the 811 new sleep screens came from",
          "Licence checked per image at download time; recorded in the manifest with source URL and creator.")
    save(fig, "wallpapers-by-source", mode)


EXAMPLES = [
    ("hiroshige-kiso-road", "cma/111644.jpg"),
    ("redoute-french-rose", "cma/122876.jpg"),
    ("rembrandt-jan-lutma", "met/364153.jpg"),
]


def examples(src_dir):
    from PIL import Image, ImageDraw

    from x3toolkit.bmp import FOUR_LEVELS, decode_levels
    from x3toolkit.wallpaper import encode_x3, make_master

    out = os.path.join(ROOT, "assets", "wallpapers")
    os.makedirs(out, exist_ok=True)
    panels = []
    for name, rel in EXAMPLES:
        src = os.path.join(src_dir, rel)
        thumb = Image.open(src).convert("RGB")
        thumb.thumbnail((528, 792))
        master = make_master(src)
        bmp = encode_x3(master, "2bit")
        with open(os.path.join(out, f"{name}.bmp"), "wb") as f:
            f.write(bmp)
        shown = Image.frombytes("L", (528, 792), bytes(FOUR_LEVELS[v] for v in decode_levels(bmp)))
        shown.save(os.path.join(out, f"{name}-x3.png"), optimize=True)
        panels.append((thumb, master, shown))

    w, h, gap = 264, 396, 16
    sheet = Image.new("RGB", (3 * w + 4 * gap, len(panels) * (h + gap) + gap + 28), (252, 252, 251))
    d = ImageDraw.Draw(sheet)
    for i, label in enumerate(["source (CC0)", "8-bit master: trim + crop", "2-bit panel output"]):
        d.text((gap + i * (w + gap), 8), label, fill=(82, 81, 78))
    for r, (thumb, master, shown) in enumerate(panels):
        yy = 28 + gap + r * (h + gap)
        t2 = thumb.copy()
        t2.thumbnail((w, h))
        sheet.paste(t2, (gap + (w - t2.width) // 2, yy + (h - t2.height) // 2))
        sheet.paste(master.resize((w, h)).convert("RGB"), (2 * gap + w, yy))
        sheet.paste(shown.resize((w, h), Image.Resampling.NEAREST).convert("RGB"), (3 * gap + 2 * w, yy))
    sheet.save(os.path.join(out, "pipeline-examples.png"), optimize=True)

    # 1:1 detail with 3x nearest-neighbour zoom: what four gray levels + error diffusion look like
    _, master, shown = panels[1]
    box = (180, 300, 340, 420)
    zm = master.crop(box).resize((480, 360), Image.Resampling.NEAREST)
    zs = shown.crop(box).resize((480, 360), Image.Resampling.NEAREST)
    z = Image.new("RGB", (480 * 2 + 3 * gap, 360 + 2 * gap + 20), (252, 252, 251))
    ImageDraw.Draw(z).text((gap, 6), "master (256 levels), 3x", fill=(82, 81, 78))
    ImageDraw.Draw(z).text((2 * gap + 480, 6), "panel (4 levels, Floyd-Steinberg), 3x", fill=(82, 81, 78))
    z.paste(zm.convert("RGB"), (gap, 20 + gap))
    z.paste(zs.convert("RGB"), (2 * gap + 480, 20 + gap))
    z.save(os.path.join(out, "dither-detail.png"), optimize=True)
    print(f"examples written to {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", metavar="SRC_DIR", help="folder with cma/ and met/ source JPEGs")
    a = ap.parse_args()
    for mode in THEMES:
        chart_stages(mode)
        chart_reads(mode)
        chart_outcomes(mode)
        chart_wallpapers(mode)
    print(f"charts written to {OUT}")
    if a.examples:
        examples(a.examples)


if __name__ == "__main__":
    main()
