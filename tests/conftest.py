import io
import os
import random
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_epub(seed: int = 1, chapters: int = 3, chapter_bytes: int = 6000) -> bytes:
    """A small synthetic EPUB with compressible but non-trivial content."""
    rnd = random.Random(seed)
    words = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit",
             "sed", "do", "eiusmod", "tempor", "incididunt", "labore", "magna", "aliqua"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.opf", "<package/>", compress_type=zipfile.ZIP_DEFLATED)
        for c in range(chapters):
            text = " ".join(rnd.choice(words) + str(rnd.randint(0, 999)) for _ in range(chapter_bytes // 8))
            z.writestr(f"text/ch{c}.xhtml", f"<html><body><p>{text}</p></body></html>",
                       compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("images/noise.bin", bytes(rnd.getrandbits(8) for _ in range(3000)),
                   compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


@pytest.fixture
def epub_bytes():
    return make_epub()
