# Customising CrossPoint on the X3

This is a short catalogue of the settings that matter on an X3, worked out by reading the
CrossPoint source at the `v1.5.0` and `2.0.0` tags.

- **Change settings on the device if you can.** The menu applies the same validation as the loader.
- **Classes:**
  - **(a)** safe, recommended;
  - **(b)** personal preference;
  - **(c)** test it yourself first;
  - **(d)** not available.

## Reading

| Setting (`settings.json` key) | Values | Class | Note |
|---|---|---|---|
| Hyphenation (`hyphenationEnabled`) | 0/1 | (a) | Use it with Justify; it noticeably evens out spacing on a narrow page |
| Paragraph alignment (`paragraphAlignment`) | Justify, Left, Center, Right, Book's style | (b) | |
| Screen margin (`screenMargin`) | 5–40, step 5 | (b) | |
| Font family | built-in Noto Sans; SD `.cpfont` families in `/.fonts/<Family>/` | (a) | In 2.0.0 **Noto Serif is an SD font**. Install it before upgrading |
| Text anti-aliasing (`textAntiAliasing`) | 0/1 | (b) | Gray glyph edges; page turns are slightly slower |
| Embedded CSS (`embeddedStyle`) | 0/1 | (b) | |
| Focus reading (`focusReadingEnabled`) | 0/1 | (b) | Bolds the start of each word |

## Display and power

| Setting | Values | Class | Note |
|---|---|---|---|
| Full refresh every N pages (`refreshFrequency`) | 1, 5, 10, 15 (default), 30 | (b) | Fewer refreshes means more ghosting and a little less power |
| Sleep screen (`sleepScreen`) | Dark, Light, Custom (`/.sleep`), Cover, Cover+Custom, None, Quick resume | (a) | For custom images, see [wallpapers.md](wallpapers.md) |
| Sleep timeout (`sleepTimeoutMinutes`) | 1–31; 31 = never (1.5.0) | (a) | 2.0.0 migrates this value to a new enum |
| Short power-button press (`shortPwrBtn`) | Ignore, Sleep, Page turn, Force refresh, Footnotes | (b) | *Force refresh* is useful with long refresh intervals |
| Hidden files (`showHiddenFiles`) | 0/1 | (a) | Turn it off to hide `.sleep`, `.crosspoint` and macOS `._*` files |
| Theme (`uiTheme`) | Classic, Lyra, Lyra Extended, RoundedRaff | (b) | |

## Status bar and clock

| Setting | Class | Note |
|---|---|---|
| Chapter page count, book percentage, progress bar, title, battery | (b) | Also editable in the web UI |
| Clock (`statusBarClock`) | (b) | The firmware lists this option as "X3 only, requires DS3231 RTC" [SRC] |
| UTC offset (`clockUtcOffsetQ`) | (a) | Quarter-hours, where 48 = UTC. 1.5.0 has no automatic daylight-saving time, so adjust it twice a year |

## Controls

- **Front-button remapping:** (b). Duplicate mappings are fixed automatically.
- **Side-button order:** (b).
- **Long-press actions** (chapter skip, orientation, bookmark, dictionary, KOReader sync): (b).
- **Tilt page turn:** (c). It appears only if the X3's motion sensor is detected.

## Getting books on and off

- **Web UI / WebDAV:** (a). File Transfer puts the device on Wi-Fi with a browser upload page.
- **Calibre wireless:** (a), with the CrossPoint Calibre plugin.
- **OPDS catalogues:** (a).
- **KOReader progress sync:** (b).
- **StarDict dictionaries in `/dictionaries/`:** (b).

## Not available

- **Reading statistics:** (d) in CrossPoint 1.5.0 and 2.0.0.
- **Front light:** (d). No front-light configuration exists for the X3 in the firmware's board table, so it appears to have none [INF].
- **SD plugins:** arrive in 2.0.0, off by default.
- **Bluetooth page turners:** 2.0.0 only. Upstream issue #4313 reports reconnection problems.

## Editing `settings.json` by hand

The 1.5.0 loader validates every key it reads:
- out-of-range enums fall back to the default;
- numbers are clamped to their range;
- unknown keys are ignored.

Editing the file offline is therefore low-risk, if you follow these rules:

1. Edit it only while the card is **out of the device**. The firmware rewrites the whole file
   whenever a setting changes [INF], so it would overwrite your edit.
2. Keep the file valid JSON, and keep a copy of the original.
3. Some keys change between versions. 2.0.0 migrates them in place, so a downgrade can reset
   some settings.
