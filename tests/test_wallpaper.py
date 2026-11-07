import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from x3toolkit.bmp import X3_HEIGHT, X3_WIDTH, validate_sleep_bmp  # noqa: E402
from x3toolkit.wallpaper import encode_x3, make_master, quantize, smart_crop, trim_borders  # noqa: E402


def artwork(tmp_path, margin=120):
    im = Image.new("RGB", (1600, 1100), (250, 248, 240))           # paper-coloured mat
    d = ImageDraw.Draw(im)
    for i in range(0, 1600 - 2 * margin, 40):
        d.line([(margin + i, margin), (1600 - margin - i // 2, 1100 - margin)], fill=(20, 40, 60), width=6)
    d.ellipse([700, 400, 1000, 700], fill=(120, 30, 30))
    p = tmp_path / "art.png"
    im.save(p)
    return p


def test_trim_and_crop(tmp_path):
    g = Image.open(artwork(tmp_path)).convert("L")
    t = trim_borders(g)
    assert t.size[0] < g.size[0] and t.size[1] < g.size[1]
    c = smart_crop(t)
    assert abs(c.size[0] / c.size[1] - X3_WIDTH / X3_HEIGHT) < 0.01


def test_master_and_encoding(tmp_path):
    m = make_master(artwork(tmp_path))
    assert m.size == (X3_WIDTH, X3_HEIGHT) and m.mode == "L"
    for preset in ("2bit", "24bit"):
        v = validate_sleep_bmp(encode_x3(m, preset))
        assert v.ok and v.direct_on_2_0 == (preset == "2bit")


def test_blank_paper_is_not_speckled():
    near_white = Image.new("L", (64, 64), 245)
    assert set(quantize(near_white)) == {255}
    mid = Image.new("L", (64, 64), 128)
    assert len(set(quantize(mid))) >= 2  # dithered, not flattened


def test_paper_tone_becomes_white_but_ink_survives():
    from x3toolkit.wallpaper import flatten_paper, paper_level
    im = Image.new("L", (200, 300), 232)               # cream paper
    ImageDraw.Draw(im).rectangle([50, 50, 150, 250], fill=40)
    assert abs(paper_level(im) - 232) <= 1
    out = flatten_paper(im)
    assert out.getpixel((10, 10)) == 255 and out.getpixel((100, 100)) < 60
    photo = Image.linear_gradient("L").resize((200, 300))  # no dominant paper tone
    assert paper_level(photo) is None and flatten_paper(photo) is photo
