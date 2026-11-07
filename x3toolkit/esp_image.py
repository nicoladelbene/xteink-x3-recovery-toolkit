"""Offline validation of ESP32-family application images (``firmware.bin``).

Mirrors the checks CrossPoint's own SD-card updater runs before it writes the
inactive OTA slot (header magic, segment table, XOR checksum, SHA-256 trailer,
chip id), so a bad download is caught on the computer before it ever reaches
the device.

Format reference: ESP-IDF ``esp_image_format.h`` (esp_image_header_t,
esp_image_segment_header_t). This module is read-only: it never talks to a
device and never writes anything.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

IMAGE_MAGIC = 0xE9
CHECKSUM_SEED = 0xEF
HEADER_LEN = 24          # esp_image_header_t
SEGMENT_HEADER_LEN = 8   # esp_image_segment_header_t
MAX_SEGMENTS = 16

# esp_chip_id_t values from ESP-IDF
CHIP_IDS = {
    0x0000: "ESP32",
    0x0002: "ESP32-S2",
    0x0005: "ESP32-C3",
    0x0009: "ESP32-S3",
    0x000C: "ESP32-C2",
    0x000D: "ESP32-C6",
    0x0010: "ESP32-H2",
    0x0012: "ESP32-P4",
}


@dataclass
class ImageReport:
    size: int
    sha256: str
    chip_id: int | None = None
    chip_name: str = "unknown"
    segments: list[tuple[int, int]] = field(default_factory=list)  # (load_addr, length)
    hash_appended: bool = False
    checksum_ok: bool = False
    sha_ok: bool | None = None
    app_version: str | None = None
    project_name: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def validate_image(data: bytes, expected_chip: int | None = None,
                   max_size: int | None = None) -> ImageReport:
    """Validate an ESP application image held in memory."""
    rep = ImageReport(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    if len(data) < HEADER_LEN + SEGMENT_HEADER_LEN:
        rep.errors.append("too small to be an ESP image")
        return rep
    if max_size is not None and len(data) > max_size:
        rep.errors.append(f"image {len(data)} B exceeds partition {max_size} B")

    magic, seg_count = data[0], data[1]
    if magic != IMAGE_MAGIC:
        rep.errors.append(f"bad magic 0x{magic:02X} (expected 0xE9)")
        return rep
    if not 1 <= seg_count <= MAX_SEGMENTS:
        rep.errors.append(f"implausible segment count {seg_count}")
        return rep

    rep.chip_id = struct.unpack_from("<H", data, 12)[0]
    rep.chip_name = CHIP_IDS.get(rep.chip_id, f"unknown(0x{rep.chip_id:04X})")
    rep.hash_appended = data[23] == 1
    if expected_chip is not None and rep.chip_id != expected_chip:
        rep.errors.append(
            f"chip id {rep.chip_name} does not match expected "
            f"{CHIP_IDS.get(expected_chip, hex(expected_chip))}")

    off = HEADER_LEN
    checksum = CHECKSUM_SEED
    for i in range(seg_count):
        if off + SEGMENT_HEADER_LEN > len(data):
            rep.errors.append(f"segment {i} header runs past end of file")
            return rep
        load, length = struct.unpack_from("<II", data, off)
        off += SEGMENT_HEADER_LEN
        if off + length > len(data):
            rep.errors.append(f"segment {i} body runs past end of file")
            return rep
        body = data[off:off + length]
        if i == 0 and length >= 256 and struct.unpack_from("<I", body, 0)[0] == 0xABCD5432:
            # esp_app_desc_t lives at the start of the first (DROM) segment
            rep.app_version = _cstr(body[16:48])
            rep.project_name = _cstr(body[48:80])
        for b in body:
            checksum ^= b
        rep.segments.append((load, length))
        off += length

    # checksum byte sits in the last byte of the 16-byte aligned block
    pad_end = (off + 16) & ~15
    if pad_end > len(data):
        rep.errors.append("checksum byte missing (truncated image)")
        return rep
    rep.checksum_ok = data[pad_end - 1] == checksum
    if not rep.checksum_ok:
        rep.errors.append(
            f"XOR checksum mismatch (stored 0x{data[pad_end - 1]:02X}, computed 0x{checksum:02X})")

    if rep.hash_appended:
        if pad_end + 32 > len(data):
            rep.errors.append("SHA-256 trailer missing (truncated image)")
            return rep
        rep.sha_ok = hashlib.sha256(data[:pad_end]).digest() == data[pad_end:pad_end + 32]
        if not rep.sha_ok:
            rep.errors.append("SHA-256 trailer mismatch")
    return rep


def validate_file(path: str, expected_chip: int | None = None,
                  max_size: int | None = None) -> ImageReport:
    with open(path, "rb") as f:
        return validate_image(f.read(), expected_chip=expected_chip, max_size=max_size)
