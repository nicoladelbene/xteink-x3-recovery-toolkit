"""Safety tests for the shell scripts, using stub `diskutil`/`mcopy`/`ddrescue` on PATH.

No test touches a real disk. Device-mode tests check that every guard refuses
before anything could be written; image-mode tests run the real write path
(mtools) against a synthetic FAT32 image file.
"""
import hashlib
import os
import shutil
import stat
import subprocess
import sys

import pytest

from fatbuilder import Fat32Builder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADD = os.path.join(ROOT, "scripts", "macos", "add-files.sh")
IMG = os.path.join(ROOT, "scripts", "macos", "image-card.sh")
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

EXTERNAL = """   Device Identifier:         disk9
   Whole:                     Yes
   Device / Media Name:       MassStorageClass
   Disk Size:                 64.0 GB (63998787584 Bytes) (exactly 124997632 512-Byte-Units)
   Device Location:           External
   Removable Media:           Removable
"""
INTERNAL = EXTERNAL.replace("External", "Internal").replace("Removable Media:           Removable",
                                                            "Removable Media:           Fixed")
PART = """   Volume Name:               NO NAME
   File System Personality:   MS-DOS FAT32
   Partition Offset:          4194304 Bytes (8192 512-Byte-Device-Blocks)
"""


def stub_env(tmp_path, disk_info=EXTERNAL, part_info=PART):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    (tmp_path / "disk.txt").write_text(disk_info)
    (tmp_path / "part.txt").write_text(part_info)
    scripts = {
        "diskutil": f"""#!/bin/sh
echo "diskutil $*" >> "{calls}"
case "$2" in
  disk9) cat "{tmp_path}/disk.txt";;
  disk9s1) cat "{tmp_path}/part.txt";;
  *) echo "   Device Location:           Internal";;
esac
""",
    }
    for tool in ("mcopy", "mmd", "mdeltree", "ddrescue", "ddrescuelog", "dd"):
        scripts[tool] = f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 0\n'
    for name, body in scripts.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", X3_PYTHON=sys.executable)
    return env, calls


def stage(tmp_path):
    s = tmp_path / "stage"
    (s / "files").mkdir(parents=True)
    rows = ["source,dest,size,sha256"]
    for i in range(3):
        data = f"wallpaper {i}".encode() * 50
        (s / "files" / f"w{i}.bmp").write_bytes(data)
        rows.append(f"files/w{i}.bmp,/.sleep/w{i:04d}.bmp,{len(data)},{hashlib.sha256(data).hexdigest()}")
    data = b"font" * 100
    (s / "files" / "f.cpfont").write_bytes(data)
    rows.append(f"files/f.cpfont,/.fonts/Fam/f.cpfont,{len(data)},{hashlib.sha256(data).hexdigest()}")
    (s / "stage-manifest.csv").write_text("\n".join(rows) + "\n")
    return s


def run(args, env, cwd=None):
    return subprocess.run([BASH, *args], capture_output=True, text=True, env=env, cwd=cwd)


DEV = ["--disk", "disk9", "--expect-size", "63998787584", "--volume-name", "NO NAME",
       "--partition-offset", "4194304"]


def calls_of(calls, tool):
    return [l for l in calls.read_text().splitlines() if l.startswith(tool)] if calls.exists() else []


def test_no_arguments_refuses(tmp_path):
    env, _ = stub_env(tmp_path)
    assert run([ADD], env).returncode == 2
    assert run([IMG], env).returncode == 2


def test_device_dry_run_writes_nothing(tmp_path):
    env, calls = stub_env(tmp_path)
    r = run([ADD, *DEV, "--stage", str(stage(tmp_path))], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DRY RUN" in r.stdout and "identity OK" in r.stdout
    assert calls_of(calls, "mcopy") == [] and calls_of(calls, "dd") == []


@pytest.mark.parametrize("disk_info,part_info,why", [
    (INTERNAL, PART, "not external"),
    (EXTERNAL.replace("63998787584 Bytes", "500107862016 Bytes"), PART, "size"),
    (EXTERNAL, PART.replace("MS-DOS FAT32", "ExFAT"), "not FAT32"),
    (EXTERNAL, PART.replace("4194304 Bytes", "1048576 Bytes"), "offset"),
    (EXTERNAL, PART.replace("NO NAME", "Macintosh HD"), "volume name"),
])
def test_device_identity_guards(tmp_path, disk_info, part_info, why):
    env, calls = stub_env(tmp_path, disk_info, part_info)
    r = run([ADD, *DEV, "--stage", str(stage(tmp_path))], env)
    assert r.returncode == 3 and "ABORT" in r.stdout and why in r.stdout
    assert calls_of(calls, "mcopy") == []


def test_device_execute_needs_exact_confirmation_and_root(tmp_path):
    env, calls = stub_env(tmp_path)
    st = str(stage(tmp_path))
    base = [ADD, *DEV, "--stage", st, "--backup", str(tmp_path / "bk"), "--execute"]
    assert run(base, env).returncode == 2
    assert run(base + ["--confirm", "WRITE disk8"], env).returncode == 2
    if os.geteuid() != 0:
        r = run(base + ["--confirm", "WRITE disk9"], env)
        assert r.returncode == 2 and "root" in r.stdout
    assert calls_of(calls, "mcopy") == [] and calls_of(calls, "dd") == []


def test_manifest_with_commas_is_refused(tmp_path):
    env, _ = stub_env(tmp_path)
    s = stage(tmp_path)
    with open(s / "stage-manifest.csv", "a") as f:
        f.write('"a,b.bmp",/.sleep/x.bmp,1,00\n')
    assert run([ADD, *DEV, "--stage", str(s)], env).returncode == 2


def test_image_card_guards(tmp_path):
    env, calls = stub_env(tmp_path, INTERNAL)
    args = [IMG, "--disk", "disk9", "--expect-size", "63998787584", "--out", str(tmp_path / "o")]
    r = run(args, env)
    assert r.returncode == 3 and "not external" in r.stdout
    second = tmp_path / "second"
    second.mkdir()
    env, calls = stub_env(second)                           # external card, but its size is refused
    r = run(args + ["--not-size", "63998787584"], env)
    assert r.returncode == 3
    assert calls_of(calls, "ddrescue") == []


@pytest.mark.skipif(shutil.which("mcopy") is None, reason="mtools not installed")
def test_image_rehearsal_end_to_end(tmp_path):
    b = Fat32Builder(clusters=8192)
    b.add_file("/A book.epub", b"PK" + os.urandom(20000))
    b.add_file("/.crosspoint/settings.json", b'{"sleepScreen":2}')
    b.add_file("/.sleep/IMG_1.bmp", b"BM" + b"1" * 3000)
    b.add_file("/.fseventsd/fseventsd-uuid", b"mac")
    img = tmp_path / "card.img"
    img.write_bytes(b.build(partition_lba=8))
    original = img.read_bytes()
    env = dict(os.environ, X3_PYTHON=sys.executable)
    st = str(stage(tmp_path))
    common = [ADD, "--image", str(img), "--partition-offset", "4096", "--stage", st]

    r = run(common, env)                                   # dry run: image untouched
    assert r.returncode == 0 and img.read_bytes() == original

    r = run(common + ["--backup", str(tmp_path / "bk"), "--execute", "--remove-macos-metadata"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PRE-WRITE CHECK: PASS" in r.stdout and "RESULT: PASS" in r.stdout and "DONE: PASS" in r.stdout
    after = img.read_bytes()

    r = run(common + ["--backup", str(tmp_path / "bk2"), "--execute"], env)   # second run is refused
    assert r.returncode != 0 and "PRE-WRITE CHECK: FAIL" in r.stdout
    assert img.read_bytes() == after


def test_make_manifest_lists_only_stage_files(tmp_path):
    s = tmp_path / "stage"
    (s / ".sleep").mkdir(parents=True)
    (s / ".sleep" / "0001.bmp").write_bytes(b"BM" + b"x" * 98)
    (s / ".sleep" / "._0001.bmp").write_bytes(b"apple double")
    r = subprocess.run([BASH, os.path.join(ROOT, "examples", "make-manifest.sh"), str(s), "/"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = (s / "stage-manifest.csv").read_text().splitlines()
    assert lines == ["source,dest,size,sha256",
                     f".sleep/0001.bmp,/.sleep/0001.bmp,100,{hashlib.sha256(b'BM' + b'x' * 98).hexdigest()}"]
    (s / "a,b.bmp").write_bytes(b"1")
    r = subprocess.run([BASH, os.path.join(ROOT, "examples", "make-manifest.sh"), str(s), "/"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and lines == (s / "stage-manifest.csv").read_text().splitlines()


# --- regressions for defects found in the 2026-11 audit -------------------------------------
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_bad_stage_is_refused_before_any_write(tmp_path, damage):
    b = Fat32Builder(clusters=8192)
    b.add_file("/book.epub", b"PK" * 5000)
    img = tmp_path / "card.img"
    img.write_bytes(b.build(partition_lba=8))
    original = img.read_bytes()
    s = stage(tmp_path)
    if damage == "missing":                               # previously: w0 written, then failure on w1
        (s / "files" / "w1.bmp").unlink()
    else:
        (s / "files" / "w1.bmp").write_bytes(b"tampered")
    env = dict(os.environ, X3_PYTHON=sys.executable)
    r = run([ADD, "--image", str(img), "--partition-offset", "4096", "--stage", str(s),
             "--backup", str(tmp_path / "bk"), "--execute"], env)
    assert r.returncode == 2 and "stage check FAILED" in r.stdout
    assert img.read_bytes() == original and not (tmp_path / "bk").exists()


def test_volume_name_is_matched_literally(tmp_path):
    env, calls = stub_env(tmp_path)
    args = ["--disk", "disk9", "--expect-size", "63998787584", "--volume-name", "NO.NAME",
            "--partition-offset", "4194304", "--stage", str(stage(tmp_path))]
    r = run([ADD, *args], env)                            # a regex would accept "NO NAME" here
    assert r.returncode == 3 and "volume name" in r.stdout


@pytest.mark.skipif(shutil.which("mcopy") is None, reason="mtools not installed")
def test_failure_during_writes_names_the_rollback_snapshot(tmp_path):
    b = Fat32Builder(clusters=8192)
    b.add_file("/book.epub", b"PK" * 5000)
    img = tmp_path / "card.img"
    img.write_bytes(b.build(partition_lba=8))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "mcopy"                              # the write step fails half-way
    fake.write_text("#!/bin/sh\necho 'mcopy: simulated I/O error' >&2\nexit 1\n")
    fake.chmod(0o755)
    env = dict(os.environ, X3_PYTHON=sys.executable, PATH=f"{bin_dir}:{os.environ['PATH']}")
    r = run([ADD, "--image", str(img), "--partition-offset", "4096", "--stage", str(stage(tmp_path)),
             "--backup", str(tmp_path / "bk"), "--execute"], env)
    assert r.returncode != 0
    assert "FAILED during/after writes" in r.stdout and "before.bin" in r.stdout
    assert r.stdout.count("FAILED") == 1                  # one banner, not one per subshell
