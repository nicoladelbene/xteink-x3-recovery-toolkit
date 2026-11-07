# Changelog

## 1.1.0 — 2026-11-07 (audit release)

An independent review of the implementation, not only of the tests and docs. Every defect below
was reproduced first, then fixed, and has a regression test that fails on 1.0.0. Interfaces are
unchanged. Re-running the reconstruction on the private reads of the original card still gives
exactly the published result: 26 intact, 6 rebuilt (byte-identical to the installed files),
2 partial, 9 not rebuilt.

**High**
- **CLI could write to a device.** `--force` let an output path such as `/dev/rdiskN` be
  opened for writing, and a symlink to a device got past the input check. Every input and
  output path is now resolved, and block or character devices are refused.
- **Directory cycle crashed the FAT32 walk.** A damaged entry pointing a directory at an
  ancestor caused infinite recursion (`RecursionError`). It is now reported as an anomaly and
  not followed.
- **Malformed ZIPs raised exceptions.** A truncated central directory raised `struct.error`;
  a member whose data runs past the end of the file raised `IndexError` in `repair_zip`.
  Both now yield a FAIL or "header-damaged" verdict.
- **Declared uncompressed size was not checked.** The docs said it was. `member_ok` and the
  reconstruction search now require exactly the declared size, reject trailing data after the
  deflate stream, and bound decompression output (no decompression bomb).
- **`add-files.sh` could leave a card partially written.** A missing or altered stage file
  was found only by `mcopy`, mid-session. Every source is now checked against its manifest
  size and SHA-256 before the card is touched, including in a dry run. Source paths with
  `..` and non-normalised destinations are refused.

**Medium**
- **Snapshot size used FAT1 only.** `used_region_end` now considers clusters allocated in
  either FAT.
- **Corrupt total-sector count.** A bad count in the boot sector could make the FAT loader read
  far beyond the FAT. The count is now bounded by the FAT's size and reported.
- **Exhaustive search for stored members** copied the whole file for every combination. It now
  reuses one buffer.
- **`fat-extract` overwrote files on name collisions** (duplicate names, or names that differ
  only in case on a case-insensitive disk). It now uses exclusive create and reports collisions.
- **Failure banner in `add-files.sh`.** An explicit abort after writes began didn't print it,
  and a pipeline subshell printed it twice. One state-aware banner now says whether anything
  was written and names the rollback snapshot.
- **Volume name was matched as a regular expression.** It is now compared literally.

**Low**
- Deleted long-name fragments are no longer fed into long-name assembly.
- File hashing during a walk reads one cluster at a time.
- `epub-reconstruct` warns when a file's cluster chain does not match its size.

**Documentation**
- **Evidence levels.** New section: decodes / CRC-32 / byte-identical to an independent copy /
  semantically correct. CRC-32 is no longer described as proof; a false-acceptance bound
  (≈ 4 × 10⁻⁶ for the 6 rebuilds) is given, and the bytes no checksum covers are listed.
- **New page `docs/safety-and-rollback.md`:** snapshot coverage, interrupted sessions,
  file-level and block-level undo (rehearsed on synthetic images), and known limits of the
  guards.
- **Device-level claims** are limited to what was checked on the device. The rollback is
  marked as prepared but not tested.
- **Reproducible quick start:** `examples/simulate_failing_card.py`, exercised in CI.

## 1.0.0 — 2026-11-07

First public release.
