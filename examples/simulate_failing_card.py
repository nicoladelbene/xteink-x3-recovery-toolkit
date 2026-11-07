"""Simulate a card that returns silently different data on every read, then recover a book.

    python examples/simulate_failing_card.py [WORKDIR]

Everything is synthetic and stays in WORKDIR (default: ./x3-demo). No device is touched.
It builds a FAT32 image holding one generated EPUB and makes 7 "reads" of it. A few weak
sectors return wrong data without any I/O error. The error model mirrors what was measured
on the real card: the errors are *correlated*. Each weak sector has a wrong version that
comes back in most reads (so a majority vote picks it), the correct data comes back in only
one or two reads, and read 1 is never fully right. With independent random flips a vote can
work; it did not on the real card. Then it runs the toolkit's own commands:

    reads-diff        -> how many sectors disagree between reads
    epub-check        -> the book fails its ZIP CRC-32 check in read 1
    reads-vote        -> a bitwise majority vote, for comparison
    epub-reconstruct  -> CRC-guided choice of sector versions

The FAT32 image is made with the test suite's synthetic builder (tests/fatbuilder.py).
"""
from __future__ import annotations

import io
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

from conftest import make_epub  # noqa: E402  (synthetic EPUB generator used by the tests)
from fatbuilder import Fat32Builder  # noqa: E402
from x3toolkit.cli import main as x3  # noqa: E402
from x3toolkit.fat32 import Fat32Volume  # noqa: E402


def simulate(workdir: str, seed: int = 7, weak_sectors: int = 4, reads: int = 7) -> int:
    os.makedirs(workdir, exist_ok=True)
    if os.listdir(workdir):
        sys.exit(f"{workdir} is not empty; choose a new directory")
    rnd = random.Random(seed)
    book = make_epub(seed=seed, chapters=4, chapter_bytes=9000)
    b = Fat32Builder()
    b.add_file("/A Synthetic Book.epub", book)
    clean = b.build()

    vol = Fat32Volume(io.BytesIO(clean))
    ent = next(e for e in vol.walk() if e.path.endswith(".epub"))
    offs = vol.sector_offsets(ent)
    weak = rnd.sample(offs[1:-2], weak_sectors)      # sectors inside the book's data

    sticky = {}                                       # the wrong version each weak sector "prefers"
    for off in weak:
        bad = bytearray(clean[off:off + 512])
        for _ in range(rnd.randint(1, 3)):
            bad[rnd.randrange(512)] ^= 1 << rnd.randrange(8)
        sticky[off] = bytes(bad)
    good_read = {off: rnd.randrange(1, reads) for off in weak}  # the one read that gets it right

    names = []
    for k in range(reads):
        img = bytearray(clean)
        for off in weak:
            if k == good_read[off]:
                continue                              # correct data, this time only
            if rnd.random() < 0.25:                   # occasionally a different wrong version
                img[off + rnd.randrange(512)] ^= 1 << rnd.randrange(8)
            else:
                img[off:off + 512] = sticky[off]
        name = "card.img" if k == 0 else f"read-{k + 1:02d}.bin"
        with open(os.path.join(workdir, name), "wb") as f:
            f.write(img)
        names.append(name)

    os.chdir(workdir)                                 # keep the printed commands short

    def run(*argv: str) -> int:
        print(f"\n$ python -m x3toolkit {' '.join(repr(a) if ' ' in a else a for a in argv)}", flush=True)
        try:
            return x3(list(argv))
        finally:
            sys.stdout.flush()
            sys.stderr.flush()

    paths = names
    ext = "read1"

    run("reads-diff", *paths)
    run("fat-extract", paths[0], ext)
    run("epub-check", f"{ext}/A Synthetic Book.epub")
    run("reads-vote", "voted.img", *paths)
    run("fat-extract", "voted.img", "voted")
    run("epub-check", "voted/A Synthetic Book.epub")
    rc = run("epub-reconstruct", paths[0], "--reads", *paths[1:],
             "--path", "/A Synthetic Book.epub", "--out", "rebuilt.epub")
    if rc == 0:
        with open("rebuilt.epub", "rb") as f:
            same = f.read() == book
        print(f"\nrebuilt.epub is {'byte-identical to' if same else 'DIFFERENT from'} the original book. "
              "That can be checked here only because the data is synthetic; on a real card the "
              "evidence is the CRC-32 and size of every member (see docs/recovery-method.md).")
    return rc


if __name__ == "__main__":
    sys.exit(simulate(sys.argv[1] if len(sys.argv) > 1 else "x3-demo"))
