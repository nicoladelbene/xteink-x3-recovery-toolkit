import hashlib

import pytest

from fatbuilder import Fat32Builder
from test_esp_image import make_image
from x3toolkit.bmp import X3_HEIGHT, X3_WIDTH, encode_bmp
from x3toolkit.cli import main


def test_refuses_block_devices():
    with pytest.raises(SystemExit):
        main(["fat-ls", "/dev/disk0"])
    with pytest.raises(SystemExit):
        main(["reads-diff", "/dev/rdisk0", "/dev/rdisk1"])


def test_fat_ls_extract_and_verify(tmp_path, capsys):
    b = Fat32Builder()
    b.add_file("/x/a.txt", b"hello")
    img = tmp_path / "v.img"
    img.write_bytes(b.build(partition_lba=8))
    assert main(["fat-ls", str(img), "--lba", "8"]) == 0
    assert "/x/a.txt" in capsys.readouterr().out
    out = tmp_path / "out"
    assert main(["fat-extract", str(img), str(out), "--lba", "8"]) == 0
    assert (out / "x" / "a.txt").read_bytes() == b"hello"
    with pytest.raises(SystemExit):              # never extracts into a non-empty directory
        main(["fat-extract", str(img), str(out), "--lba", "8"])
    man = tmp_path / "m.csv"
    man.write_text("source,dest,size,sha256\n")
    assert main(["verify", str(img), str(img), "--lba", "8", "--manifest", str(man)]) == 0
    assert main(["precheck", str(img), "--lba", "8", "--manifest", str(man), "--require", "/x/a.txt"]) == 0
    assert main(["precheck", str(img), "--lba", "8", "--manifest", str(man), "--require", "/nope"]) == 1


def test_firmware_and_bmp_commands(tmp_path, capsys):
    fw = tmp_path / "firmware.bin"
    fw.write_bytes(make_image())
    digest = hashlib.sha256(fw.read_bytes()).hexdigest()
    assert main(["firmware-check", str(fw), "--chip", "ESP32-C3", "--sha256", digest]) == 0
    assert main(["firmware-check", str(fw), "--chip", "ESP32-S3"]) == 1
    assert main(["firmware-check", str(fw), "--sha256", "0" * 64]) == 1
    bmp = tmp_path / "w.bmp"
    bmp.write_bytes(encode_bmp(bytes(X3_WIDTH * X3_HEIGHT), X3_WIDTH, X3_HEIGHT, 2))
    assert main(["bmp-validate", str(bmp)]) == 0
    assert "direct-on-2.0.0" in capsys.readouterr().out


# --- regressions for defects found in the 2026-11 audit -------------------------------------
def test_no_device_output_even_with_force_and_no_symlinked_device(tmp_path):
    import os
    r = tmp_path / "r.bin"
    r.write_bytes(b"x" * 4096)
    link = tmp_path / "card"
    os.symlink("/dev/null", link)                       # stands in for /dev/rdiskN
    for argv in (["reads-vote", "/dev/null", str(r), str(r), "--force"],
                 ["reads-vote", str(link), str(r), str(r), "--force"],
                 ["reads-diff", str(link), str(r)],
                 ["epub-check", str(link)],
                 ["fat-ls", str(link)]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "never devices" in str(e.value)


def test_extract_never_overwrites_on_name_collision(tmp_path):
    b = Fat32Builder()
    b.add_file("/x1.txt", b"first")
    b.add_file("/x2.txt", b"second")
    img = bytearray(b.build())
    i = img.find("x2".encode("utf-16-le"))               # long-name fragment of the second file
    assert i > 0
    img[i:i + 4] = "x1".encode("utf-16-le")               # damaged dir: two entries, same name
    p = tmp_path / "v.img"
    p.write_bytes(bytes(img))
    out = tmp_path / "out"
    assert main(["fat-extract", str(p), str(out)]) == 1
    assert (out / "x1.txt").read_bytes() == b"first"
