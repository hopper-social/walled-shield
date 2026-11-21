# Sound Credits

This folder contains sounds for Pip. All sounds should be licensed for redistribution.

## Required Sounds (13 total)

| File | When it plays | Source | License |
|------|---------------|--------|---------|
| `alert.mp3` | Sensitive data held back / Block rule fired | TBD | TBD |
| `knock.mp3` | Claude is waiting for Allow/Block | TBD | TBD |
| `ok.mp3` | Telegram paired / vault ready / test passed | TBD | TBD |
| `info.mp3` | Routine news (Continue chosen, warn rule) | TBD | TBD |
| `hello.mp3` | Pip arrives or says hi | TBD | TBD |
| `boop.mp3` | You click Pip | TBD | TBD |
| `giggle.mp3` | Three quick boops | TBD | TBD |
| `love.mp3` | Long petting → heart eyes | TBD | TBD |
| `grumble.mp3` | Too much petting / scrubbed too fast | TBD | TBD |
| `beg.mp3` | Pip asks to be petted | TBD | TBD |
| `purr.mp3` | While you pet him (theme: Purr) - loop | TBD | TBD |
| `chime.mp3` | Each stroke (theme: Chimes) | TBD | TBD |
| `bubble.mp3` | Each stroke (theme: Bubbles) | TBD | TBD |

## Search Tips

See `docs/SOUNDS.md` for detailed search terms and specifications.

Recommended sources:
- **Pixabay** (Pixabay Content License) - https://pixabay.com/sound-effects/
- **Freesound** (CC0 only) - https://freesound.org/search/?q=&f=is_license%3Atrue+
- **Zapsplat** (standard license with attribution) - https://www.zapsplat.com/

## Format Requirements

- **Format:** MP3 or OGG, mono, 44.1/48 kHz
- **Size:** Under 60 KB each (except purr loop)
- **Length:** Max 1.5s (except purr loop which is 2-4s looped)
- **Levels:** Normalized to ~-20 LUFS integrated, peaks ≤ -3 dBFS
- **Character:** Warm, rounded, soft-attack. C major / A minor pentatonic.

## Fallback

If sounds are missing, Walled uses synthesized fallback sounds automatically.
