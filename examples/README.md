# Examples

## 1. Rehearse a card update on a synthetic image (no hardware, no root)

```bash
REPO=$PWD                                                # the cloned repository
mkdir -p ~/x3demo/stage/.sleep && cd ~/x3demo
python -m x3toolkit bmp-convert --out stage/.sleep path/to/cc0-artwork.jpg
"$REPO"/examples/make-manifest.sh stage /                # -> stage/stage-manifest.csv
# a 64 MiB FAT32 image with the partition at 1 MiB (mtools)
dd if=/dev/zero of=card.img bs=1048576 count=64
mformat -i card.img@@1048576 -F -c 1 -T 129024 -h 64 -s 32 ::
"$REPO"/scripts/macos/add-files.sh --image card.img --partition-offset 1048576 --stage stage     # dry run
"$REPO"/scripts/macos/add-files.sh --image card.img --partition-offset 1048576 --stage stage \
    --backup ./snapshots --execute                       # snapshots, add, verify -> RESULT: PASS
```

This sequence was run by hand before release; the test suite runs the same write path against a synthetic FAT32 image.

## 2. The same against a real card (macOS)

```bash
diskutil list external                                  # find the card: say disk4
scripts/macos/add-files.sh --disk disk4 --expect-size 63998787584 --volume-name "NO NAME" \
    --partition-offset 4194304 --stage ./stage          # dry run: identity + plan only
sudo scripts/macos/add-files.sh --disk disk4 --expect-size 63998787584 --volume-name "NO NAME" \
    --partition-offset 4194304 --stage ./stage --backup ./snapshots \
    --execute --confirm "WRITE disk4"
```

Get the exact size and partition offset from `diskutil info disk4` and `diskutil info disk4s1`.
The script refuses to run if any of them, or the volume name, differs.

## 3. Triage an image of a failing card

```bash
python -m x3toolkit reads-diff card.img reads/read-02.bin reads/read-03.bin --mib 96
python -m x3toolkit fat-ls card.img --lba 8192 --csv listing.csv
python -m x3toolkit fat-extract card.img --lba 8192 ./extracted
python -m x3toolkit epub-check ./extracted/*.epub
python -m x3toolkit epub-reconstruct card.img --lba 8192 --reads reads/read-*.bin \
    --path "/Some Book.epub" --out "./repaired/Some Book.epub"
```

`stage-manifest.example.csv` shows the manifest format: `source,dest,size,sha256`. File names
must not contain commas.
