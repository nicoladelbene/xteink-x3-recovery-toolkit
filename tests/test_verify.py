import hashlib
import io

from fatbuilder import Fat32Builder
from x3toolkit.verify import Plan, compare, walk_both_fats

BOOK = b"PK" + b"x" * 3000
NEW = b"BM" + b"n" * 900


def card(extra=(), metadata=True, book=BOOK):
    b = Fat32Builder()
    b.add_file("/A book.epub", book)
    b.add_file("/.crosspoint/settings.json", b"{}")
    b.add_file("/.sleep/IMG_1.bmp", b"BM1")
    b.add_file("/.sleep/IMG_2.bmp", b"BM2")
    if metadata:
        b.add_file("/.fseventsd/uuid", b"x")
    for p, d in extra:
        b.add_file(p, d)
    return b.build(partition_lba=8)


def tree(img):
    ents, problems = walk_both_fats(io.BytesIO(img), 8)
    assert problems == []
    return ents


PLAN = Plan.from_manifest("source,dest,size,sha256\nw.bmp,/.sleep/w0001.bmp,902,"
                          + hashlib.sha256(NEW).hexdigest() + "\n",
                          removable=("/.fseventsd",), ordered_dirs=("/.sleep",))


def test_planned_change_passes():
    rep = compare(tree(card()), tree(card([("/.sleep/w0001.bmp", NEW)], metadata=False)), PLAN)
    assert rep.ok, rep.problems
    assert rep.added == 1 and rep.removed >= 1


def test_any_unplanned_change_fails():
    before = tree(card())
    changed = tree(card([("/.sleep/w0001.bmp", NEW)], metadata=False, book=BOOK[:-1] + b"y"))
    assert any(p.startswith("CHANGED: /A book.epub") for p in compare(before, changed, PLAN).problems)
    extra = tree(card([("/.sleep/w0001.bmp", NEW), ("/stray.txt", b"?")], metadata=False))
    assert "UNEXPECTED: /stray.txt" in compare(before, extra, PLAN).problems
    missing = tree(card(metadata=False))
    assert "NOT ADDED: /.sleep/w0001.bmp" in compare(before, missing, PLAN).problems
    kept_meta = tree(card([("/.sleep/w0001.bmp", NEW)]))
    assert any(p.startswith("NOT REMOVED") for p in compare(before, kept_meta, PLAN).problems)
    bad = tree(card([("/.sleep/w0001.bmp", NEW[:-1] + b"!")], metadata=False))
    assert "BAD CONTENT: /.sleep/w0001.bmp" in compare(before, bad, PLAN).problems
