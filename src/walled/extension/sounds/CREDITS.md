# Sound Credits

This folder contains sounds for Pip. The current files are **placeholder MP3s** - they are valid MP3 files but contain silence. Walled will use synthesized fallback sounds until real sounds are added.

## Required Sounds (13 total)

| File | When it plays | Status |
|------|---------------|--------|
| `alert.mp3` | Sensitive data held back / Block rule fired | Placeholder |
| `knock.mp3` | Claude is waiting for Allow/Block | Placeholder |
| `ok.mp3` | Telegram paired / vault ready / test passed | Placeholder |
| `info.mp3` | Routine news (Continue chosen, warn rule) | Placeholder |
| `hello.mp3` | Pip arrives or says hi | Placeholder |
| `boop.mp3` | You click Pip | Placeholder |
| `giggle.mp3` | Three quick boops | Placeholder |
| `love.mp3` | Long petting → heart eyes | Placeholder |
| `grumble.mp3` | Too much petting / scrubbed too fast | Placeholder |
| `beg.mp3` | Pip asks to be petted | Placeholder |
| `purr.mp3` | While you pet him (theme: Purr) - loop | Placeholder |
| `chime.mp3` | Each stroke (theme: Chimes) | Placeholder |
| `bubble.mp3` | Each stroke (theme: Bubbles) | Placeholder |

## Getting Real Sounds

See `docs/SOUNDS.md` for detailed specifications and search terms.

### Recommended Sources

1. **Pixabay** (Pixabay Content License) - https://pixabay.com/sound-effects/
   - Free for commercial use
   - No attribution required

2. **Freesound** (CC0 only) - https://freesound.org/search/?f=is_license%3Atrue+
   - Filter by "Creative Commons 0" license
   - Free for any use

3. **Zapsplat** (standard license with attribution) - https://www.zapsplat.com/
   - Free with account
   - Requires attribution

### Search Terms by Sound

| Sound | Search Terms |
|-------|--------------|
| alert | "soft notification two tone", "kalimba notification" |
| knock | "soft wood knock", "double tap wood" |
| ok | "success chime marimba", "celesta success" |
| info | "soft bell ping", "glass notification" |
| hello | "cute robot chirp", "friendly robot beep" |
| boop | "soft pop UI", "bubble pop" |
| giggle | "cute robot laugh", "tiny chirps" |
| love | "magic sparkle chime", "celesta glissando" |
| grumble | "cartoon grumble soft", "robot hmph" |
| beg | "cute question chirp", "pleading chirp" |
| purr | "cat purr loop", "purring seamless" |
| chime | "single wind chime", "chime strike" |
| bubble | "single bubble bloop", "water drop pop" |

## Format Requirements

- **Format:** MP3 or OGG, mono, 44.1/48 kHz
- **Size:** Under 60 KB each (except purr loop)
- **Length:** Max 1.5s (except purr loop which is 2-4s looped)
- **Levels:** Normalized to ~-20 LUFS integrated, peaks ≤ -3 dBFS
- **Character:** Warm, rounded, soft-attack. C major / A minor pentatonic.

## Fallback

Walled uses synthesized fallback sounds automatically when files are missing or invalid. The app works out of the box without real sounds.
