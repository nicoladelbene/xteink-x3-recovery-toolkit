# Security and safety

These tools read and write storage devices. The design rules:

- **Images only, never devices.** The Python CLI refuses any path, input or output, that is
  under `/dev` or resolves (through symlinks) to a block or character device, even with
  `--force`. Inputs are opened read-only (raw reads are memory-mapped with `ACCESS_READ`).
- **Dry run by default.** `scripts/macos/*.sh` print the plan and exit unless `--execute` is given.
  Writing to a real device additionally needs root and `--confirm "WRITE diskN"` naming the disk.
- **Identity checks** before and again immediately before any write (whole disk, external,
  removable, exact byte size, filesystem, partition offset, volume name, not mounted).
- **Rollback snapshot first**, verified by a second read, and never overwritten.
- **Stage checked first**: every staged file must match its manifest size and SHA-256 before the
  card is touched, so a session cannot stop half-way because of a bad stage.
- **Verification after**: both FAT copies of the before/after snapshots are walked and compared;
  anything that changed but was not planned is a failure.

What the guards do not cover, and what to do after an interrupted session:
[docs/safety-and-rollback.md](docs/safety-and-rollback.md).

If you find a way to make a script write to the wrong device, or a command that modifies its
input, please open a GitHub issue (or, if you prefer, a private security advisory on the repo).
There are no network services, credentials or secrets in this project.
