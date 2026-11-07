import pytest

from x3toolkit.bmp import (FOUR_LEVELS, X3_HEIGHT, X3_WIDTH, decode_levels, display_level,
                           encode_bmp, parse_header, validate_sleep_bmp)


def gradient(levels=FOUR_LEVELS):
    return bytes(levels[(x * len(levels)) // X3_WIDTH] for _ in range(X3_HEIGHT) for x in range(X3_WIDTH))


def test_two_bit_layout_matches_firmware_writer():
    data = encode_bmp(gradient(), X3_WIDTH, X3_HEIGHT, 2)
    assert len(data) == 104_614
    h = parse_header(data)
    assert (h.width, h.height, h.top_down, h.bpp, h.compression, h.off_bits) == (528, 792, True, 2, 0, 70)
    assert data[54:70] == bytes.fromhex("0000000055555500aaaaaa00ffffff00")


def test_palette_luminance_maps_to_panel_levels():
    assert [display_level((v, v, v)) for v in FOUR_LEVELS] == [0, 1, 2, 3]
    assert display_level((128, 128, 128)) == 2  # not a midpoint: >> 6 truncates


def test_decode_round_trip_top_down_and_bottom_up():
    g = gradient()
    want = [FOUR_LEVELS.index(v) for v in g]
    for td in (True, False):
        assert decode_levels(encode_bmp(g, X3_WIDTH, X3_HEIGHT, 2, top_down=td)) == want


def test_validation_flags_conversion_on_2_0():
    v2 = validate_sleep_bmp(encode_bmp(gradient(), X3_WIDTH, X3_HEIGHT, 2))
    assert v2.ok and v2.direct_on_2_0
    v24 = validate_sleep_bmp(encode_bmp(bytes(i % 256 for i in range(X3_WIDTH * X3_HEIGHT)),
                                        X3_WIDTH, X3_HEIGHT, 24, top_down=False))
    assert v24.ok and not v24.direct_on_2_0
    assert len(encode_bmp(bytes(X3_WIDTH * X3_HEIGHT), X3_WIDTH, X3_HEIGHT, 24)) == 1_254_582


def test_validation_errors():
    good = bytearray(encode_bmp(gradient(), X3_WIDTH, X3_HEIGHT, 2))
    rle = bytearray(good)
    rle[30] = 1  # BI_RLE8
    assert not validate_sleep_bmp(bytes(rle)).ok
    assert not validate_sleep_bmp(bytes(good[:5000])).ok
    assert not validate_sleep_bmp(b"GIF89a" + bytes(100)).ok
    small = validate_sleep_bmp(encode_bmp(bytes(16), 4, 4, 2))
    assert small.ok and not small.direct_on_2_0 and small.warnings


def test_encoder_rejects_values_outside_palette():
    with pytest.raises(ValueError):
        encode_bmp(bytes([100]) * 16, 4, 4, 2)
