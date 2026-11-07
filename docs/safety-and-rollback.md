# Safety model, snapshots and rollback

This page describes what the write path (`scripts/macos/add-files.sh`) protects against, what
it does not, and what to do when a session stops part-way. Read it before writing to a card
whose contents you can't replace.

## What protects the card

| Stage | Guard | Failure means |
|---|---|---|
| Before anything | Arguments validated. **Every staged file must exist and match its manifest size and SHA-256**, and paths must be normalised | nothing read, nothing written |
| Identity | Whole disk, external, removable, exact byte size, FAT32 at the given offset, exact volume name, not mounted | nothing written |
| Authorisation (device mode) | `--execute`, root, and `--confirm "WRITE diskN"` naming the same disk | nothing written |
| Snapshot | used region copied to `before.bin`, re-read and compared by SHA-256 | nothing written |
| Pre-write gate | snapshot parses through both FATs with no anomalies, the two FATs give the same tree, and no planned path exists yet | nothing written |
| Just before writing | identity checked again | nothing written |
| Writing | `mcopy -D s`: an existing name is skipped, never overwritten | **partial**: see below |
| After | `after.bin` snapshot (re-read); `fsck_msdos -n` (read-only) must be clean; `x3toolkit verify` | written but not accepted; not ejected |

`x3toolkit verify` walks both snapshots through **both** FAT copies. It fails on any of these:
- a pre-existing file whose size or SHA-256 changed, or that disappeared;
- a planned file that is missing or has the wrong SHA-256;
- anything else that appeared;
- the two FATs disagreeing about the tree.

The Python CLI never opens a device, either for reading or writing. Paths are resolved
through symlinks, and block or character devices are refused even with `--force`.

## What a snapshot covers, and what it doesn't

`before.bin` covers byte 0 of the disk up to just past the highest cluster allocated in
**either** FAT copy, plus 1 MiB. That range includes the partition table, the boot sector,
FSInfo, both FATs, every directory and every allocated cluster.

- **Not covered:** free clusters above that point. That doesn't matter for rollback, because
  restoring `before.bin` marks them free again. It does mean the snapshot isn't a forensic
  image: deleted data in free space beyond it is not preserved.
- **Changes after the session:** anything written to the card after the session (for
  example, the reader saving a page position) is not in `before.bin`. Restoring it discards
  those changes.

## What `verify` does not check

- **Content inside files** is compared by SHA-256, not interpreted. A wrong but planned file
  passes if its hash is in the manifest.
- **Unreferenced metadata:** free-cluster contents, deleted directory entries, timestamps and
  attributes. `fsck_msdos -n` covers the structural part of this.
- **Damage unrelated to the session:** if the card itself returns bad data, both snapshots are
  re-read to catch that while the session runs. Problems that appear later are not detected.

## When a session stops part-way

The banner says which case you are in:

- **`FAILED before any write`**: nothing was written. Fix the cause and run again.
- **`FAILED during/after writes`**: some planned files may be on the card. The card is not
  ejected. Then:
  1. **Do nothing to the card yet**, and in particular don't let macOS mount it. Re-running the
     same manifest is refused on purpose, because planned paths now exist.
  2. Take a new read-only snapshot and compare it with `before.bin`. Size the snapshot the
     same way the script does, because files added by the session can lie beyond the old used
     region. `N`, `LBA` and `MIB` are placeholders:

     ```bash
     sudo dd if=/dev/rdiskN of=head.bin bs=1m count=64                    # read only
     MIB=$(python -m x3toolkit fat-used head.bin --lba LBA)
     sudo dd if=/dev/rdiskN of=card-now.bin bs=1m count="$MIB"            # read only
     python -m x3toolkit verify before.bin card-now.bin --lba LBA --manifest stage/stage-manifest.csv
     ```

     `verify` fails, and prints `NOT ADDED: <path>` for each planned file that didn't arrive.
     That is the normal result of an interrupted copy. **Any other problem line**
     (`CHANGED`, `MISSING`, `UNEXPECTED`, `BAD CONTENT`, FAT disagreement) means more than
     the planned additions changed. Stop and roll back at block level.

  3. Then choose one of these:
     - **Finish:** build a manifest of the files that are still missing and run a new session
       with it.
     - **Undo at file level** (preferred): delete exactly the planned paths that arrived,
       using `sudo env MTOOLS_SKIP_CHECK=1 mdel -i /dev/diskNs1 ::/path`. Then remove, with
       `mrd`, any directory the session created that is now empty. `verify` lists these as
       `UNEXPECTED: /dir`. Finally take a fresh snapshot and verify it against `before.bin`
       with an empty manifest (just the `source,dest,size,sha256` header line): the result must
       be `RESULT: PASS`. This exact sequence was rehearsed on a synthetic image.
     - **Undo at block level** (last resort): write `before.bin` back to the **same** card.
       This is the one raw write in the procedure. The toolkit deliberately does not automate
       it. If you must, first confirm the identity yourself with `diskutil info diskN`, then
       `sudo dd if=before.bin of=/dev/rdiskN bs=1m`, then take a fresh snapshot and
       `verify before.bin fresh.bin` with an empty manifest. `diskN` here is a placeholder: the
       command fails as written.

## Known limits of the guards

- **Disk numbers can be reused.** Identity is checked before the snapshot and again just
  before writing, but not between individual files. If the card is removed during the copy and
  another disk appears under the same `diskN`, `mcopy` writes to whatever is there. The window
  lasts as long as the copy takes. Don't plug or unplug disks during a session.
- **Automounting:** the mount check runs at identity time. If macOS mounts the volume during
  the session, two writers would share it. Run `mount-blocker` (it refuses automounts of
  external disks) for the whole session. It doesn't stop a mount someone starts deliberately.
- **Running out of space mid-copy** is not checked in advance; `mcopy` fails and you are in
  the *during writes* case above.
- **The manifest format is simple CSV:** no commas or quotes in names. Such rows are refused
  rather than misread.
