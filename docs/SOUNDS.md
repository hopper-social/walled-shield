# Pip's sounds: what to record or license

Walled ships synthesized fallback sounds so it works out of the box. Recorded sounds will feel richer. Drop files with the names below into the sounds folder, and Pip uses them instead; anything you don't provide keeps its fallback.

| Where | Folder |
|---|---|
| Desktop Pip and the app (per user, no rebuild) | macOS `~/Library/Application Support/WalledGuard/sounds/` · Linux `~/.local/state/walled-guard/sounds/` |
| Shipped with Walled (desktop + browser Pip) | `src/walled/extension/sounds/`, and list the files in `index.json` (`["alert.mp3", "purr.mp3", …]`) |

**Format:**
- **Files:** MP3 or OGG, mono, 44.1/48 kHz, at most 1.5 s (except `purr`), under 60 KB each.
- **Levels:** loudness normalised to about −20 LUFS integrated with peaks ≤ −3 dBFS, so no sound jumps out. Alerts are about 3 dB louder than the rest, never more.
- **Edges:** no silence before the attack (Pip reacts instantly), and a 10–20 ms fade at the end.

**Character, for the whole set:** warm, rounded, soft-attack. Think of a friendly UI from a well-made game or a premium OS, made with wood, glass and felt-mallet instruments. Avoid buzzers, sirens, beeps, 8-bit chiptune, cartoon "boing", and anything that sounds like an error. Everything in one key (C major / A minor pentatonic), so sounds never clash when two play close together.

## The 13 sounds

| File | When it plays | What it should feel like | Length | Search terms (Pixabay / Freesound / Zapsplat) |
|---|---|---|---|---|
| `alert.mp3` | Sensitive data held back · a Block rule fired | **"Hey, look"**: calm but clearly noticeable. Two warm notes, low then higher (A4 → E5), soft mallet or muted kalimba, slight room reverb. It must not raise anyone's pulse. | 0.6–0.9 s | "soft notification two tone", "kalimba notification", "gentle attention chime", "warm UI alert" |
| `knock.mp3` | Claude is waiting for your Allow / Block | **A polite knock**: two soft taps on wood, or a muted marimba double-tap. "When you have a moment." | 0.3–0.5 s | "soft wood knock UI", "double tap wood", "marimba tap notification" |
| `ok.mp3` | Telegram paired · vault ready · test passed | **Small success**: rising 3–4 note arpeggio, glassy marimba or celesta. Satisfying, not triumphant. | 0.5–0.8 s | "success chime marimba", "positive UI arpeggio", "celesta success" |
| `info.mp3` | Routine news (Continue chosen, a warn rule) | **A light ping**: one or two bell notes, quieter than `ok`. | 0.3–0.6 s | "soft bell ping UI", "glass notification light" |
| `hello.mp3` | Pip arrives or says hi | **A friendly chirp**: a short rising "boo-weep", like a tiny robot greeting. Breathy, not electronic-harsh. | 0.3–0.5 s | "cute robot chirp", "friendly robot beep soft", "small creature hello" |
| `boop.mp3` | You click Pip | **A soft squishy pop**: a rubbery "bwip", playful. | 0.1–0.2 s | "soft pop UI", "bubble pop cute", "squish click" |
| `giggle.mp3` | Three quick boops | **A tiny giggle**: three rising chirps, cute robot laugh. | 0.3–0.5 s | "cute robot laugh", "tiny chirps giggle" |
| `love.mp3` | Long petting → heart eyes | **Sparkle of joy**: a shimmering upward celesta or wind-chime run with a soft glow. | 0.7–1.0 s | "magic sparkle chime soft", "celesta glissando", "fairy sparkle gentle" |
| `grumble.mp3` | Too much petting · scrubbed too fast | **A grumpy "hmph"**: two low, wobbly descending notes, comic but never mean. Like a small robot pouting. | 0.3–0.5 s | "cartoon grumble soft", "robot hmph", "low wobble descending" |
| `beg.mp3` | Pip asks to be petted | **A hopeful "pleeease?"**: two rising chirps with a little upward bend at the end, like a question. | 0.4–0.6 s | "cute question chirp", "robot curious beep", "pleading chirp" |
| `purr.mp3` | While you pet him (theme: Purr) | **A seamless purr loop**: a real cat purr, cleaned and low-passed, or a soft engine-like rumble about 25 Hz modulated. It must loop with no click (Walled loops it and follows your stroke speed with volume). | 2–4 s loop | "cat purr loop", "purring seamless loop" |
| `chime.mp3` | Each stroke (theme: Chimes) | **One wind-chime strike**: a single bright aluminium chime with a long natural decay. Walled pitches it to stay in tune. | 1–2 s | "single wind chime hit", "chime strike one shot" |
| `bubble.mp3` | Each stroke (theme: Bubbles) | **One water bubble**: a round, rising "bloop". Walled varies the pitch. | 0.1–0.2 s | "single bubble bloop", "water drop pop soft" |

## Licensing
Use sounds you're allowed to redistribute, because they ship inside the app:

- Pixabay Sound Effects (Pixabay Content License);
- Freesound **CC0** only (not CC-BY-NC);
- Zapsplat standard licence with attribution;
- your own recordings.

Record the source and licence for each file in `src/walled/extension/sounds/CREDITS.md`.

## Checking them
Open Walled → **Settings → Companion (Pip)**:
- **Try petting** plays your petting theme;
- **Hear an alert** and **Hear a success** play the event sounds.

On the desktop, stroke and click Pip. Listen at low laptop volume: the alert should still be noticed and nothing should startle.
