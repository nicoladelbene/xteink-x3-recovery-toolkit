# CrossPoint 1.5.0 → 2.0.0 on the XteInk X3

These are notes from one upgrade done on 2026-11-07. The upgrade worked, and the following were
all kept: reading positions, fonts, sleep screens, and 44 books. CrossPoint is a separate project;
this repository is not affiliated with it (see [NOTICE](../NOTICE.md)).

**Labels:**
- **[SRC]** read in the firmware source at the public tag.
- **[DEV]** observed on the device.
- **[INF]** inference, not tested.

## Release facts

| | 1.5.0 | 2.0.0 |
|---|---|---|
| Release | 2026-08-07, stable | 2026-11-01, stable |
| X3/X4 image | 5,544,112 B | 5,537,104 B (`crosspoint-2.0.0-x3-x4.bin`; same bytes as that release's `firmware.bin`) |
| SHA-256 | `a7087155757bc63c1fcf60ae8d60a3760ce6d3406aaf7b9f23d0025244434f08` | `56d27fec71c6b6ec62a1bcf508cefa80805b4a63ff6eb8793ae754bfb5f8a877` |
| Partition table | two 6.25 MB OTA slots (app0 0x10000, app1 0x650000) | identical [SRC] |

The digests are GitHub's per-asset digests. The releases carry no signatures. Check what you
downloaded without writing anything:

```bash
python -m x3toolkit firmware-check crosspoint-2.0.0-x3-x4.bin --chip ESP32-C3 --max-size 0x640000 \
  --sha256 56d27fec71c6b6ec62a1bcf508cefa80805b4a63ff6eb8793ae754bfb5f8a877
```

`firmware-check` parses the ESP32 app image and checks:
- the magic bytes, the segments and the XOR checksum;
- the SHA-256 trailer;
- the chip ID;
- the `esp_app_desc` project name and version.

These are the checks the device makes before it accepts an image.

## What changes on the card

| Area | 1.5.0 → 2.0.0 | Action needed |
|---|---|---|
| Book cache key (`/.crosspoint/epub_<hash>`) | unchanged: hash of the file path [SRC] | keep books at the **same paths** |
| `progress.bin` | same format (4, 6 or 10 bytes) [SRC] | none; positions were kept [DEV] |
| Chapter layout cache | `SECTION_FILE_VERSION` 36 → 73, rebuilt the first time each book opens [SRC] | none (the first open is slower) |
| `settings.json` | some keys migrated in place (`lineSpacing`→`lineHeight`, …) [SRC] | back it up; a downgrade may reset some settings [INF] |
| Noto Serif | built in → **SD font** [SRC/release notes] | install the `NotoSerif` `.cpfont` family to `/.fonts/NotoSerif/` *before* upgrading, or every book re-lays out in Noto Sans. `CPFONT_VERSION` is 4 in both versions [SRC] |
| Sleep screens | drawn directly → only **528×792 at ≤2 bpp** is drawn directly; anything else is converted, re-dithered and cached in `/.crosspoint/sleep_*.bmp` [SRC] | use 2-bpp BMPs (see below) |
| Sleep folders | `/.sleep` **or** `/sleep` → both pooled; random choice becomes a shuffle that shows each image once per cycle [SRC] | none |

**The sleep-screen trap.** Upstream issue
[#4301](https://github.com/crosspoint-reader/crosspoint-reader/issues/4301) reports that
non-2-bit sleep BMPs come out washed out on the X3 after 2.0.0 re-dithers them.

- **Recommended:** pre-dither on the computer to the panel's four levels, as a 2-bpp top-down
  BMP with palette 0/85/170/255. Both versions draw that format directly.
- **Check before upgrading:** `x3toolkit bmp-validate` flags every file that 2.0.0 would convert.

## How the upgrade was done (no Wi-Fi, no USB flashing)

```mermaid
sequenceDiagram
    participant Mac
    participant Card as microSD
    participant X3
    Mac->>Mac: firmware-check 2.0.0 and 1.5.0 images (chip, SHA-256, size)
    Mac->>Card: add-files.sh: 2-bpp sleep BMPs, NotoSerif .cpfont,<br/>2.0.0 image, 1.5.0 rollback image (add-only, verified)
    Card->>X3: insert card, boot 1.5.0, confirm books and positions
    X3->>X3: Settings → System → SD Card Firmware Update → pick 2.0.0 image
    X3->>X3: validates image, writes the inactive OTA slot, reboots
    X3-->>Mac: [DEV] 2.0.0 boots; positions, NotoSerif and sleep screens OK
```

In 1.5.0, *SD Card Firmware Update* sits next to *Check for updates* [SRC]. Before writing, it
validates the image: magic bytes, segments, checksum, SHA-256 and chip ID. It then writes the
**inactive** OTA slot, so the previous firmware stays in the other slot until the next update.

## Rollback (prepared, not needed)

Do these in order of preference. None was carried out, because 2.0.0 worked.

1. **Swap boot partition.** crosspointreader.com/debug connects over WebSerial from a desktop
   Chromium browser and can switch otadata back to the slot that still holds 1.5.0. Connecting
   and reading the partition table is read-only, so it makes a safe check before you need it.
2. **Flash the 1.5.0 image from the SD card.** Keep it on the card, for example at
   `/firmware-rollback/crosspoint-1.5.0-x3-x4.bin`, and pick it from the SD firmware update menu.
   If the device will not reach the menu, use the firmware's recovery boot (hold **Up + Power**
   while it starts) [SRC/INF].
3. **Use the official web flasher** at crosspointreader.com with the 1.5.0 image.

**Never go below 1.5.0 on a recent X3.** Upstream
[#3145](https://github.com/crosspoint-reader/crosspoint-reader/issues/3145) explains why: newer
units have a panel that firmware ≤ 1.4.x cannot drive, so the screen stays blank.
