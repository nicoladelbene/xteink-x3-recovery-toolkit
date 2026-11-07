# Security and safety

These tools read and write storage devices. The design rules:

- **Read-only by default.** Every Python command opens images/devices read-only (mmap with
  `ACCESS_READ`); the CLI refuses `/dev/` paths outright — work from an image.
- **Dry run by default.** `scripts/macos/*.sh` print the plan and exit unless `--execute` is given.
  Writing to a real device additionally needs root and `--confirm "WRITE diskN"` naming the disk.
- **Identity checks** before and again immediately before any write (whole disk, external,
  removable, exact byte size, filesystem, partition offset, volume name, not mounted).
- **Rollback snapshot first**, verified by a second read, and never overwritten.
- **Verification after**: both FAT copies of the before/after snapshots are walked and compared;
  anything that changed but was not planned is a failure.

If you find a way to make a script write to the wrong device, or a command that modifies its
input, please open a GitHub issue (or, if you prefer, a private security advisory on the repo).
There are no network services, credentials or secrets in this project.
