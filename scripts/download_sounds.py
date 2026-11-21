#!/usr/bin/env python3
"""
Download free sounds for Pip from Pixabay.

Usage:
    python scripts/download_sounds.py

This script searches Pixabay for sounds matching the requirements in docs/SOUNDS.md
and downloads them to src/walled/extension/sounds/

You'll need to get a free Pixabay API key from https://pixabay.com/api/docs/
and set it as PIXABAY_API_KEY environment variable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOUNDS_DIR = ROOT / "src" / "walled" / "extension" / "sounds"

# Sound search terms from docs/SOUNDS.md
SOUNDS = {
    "alert.mp3": {
        "q": "soft notification two tone",
        "description": "Calm but noticeable, two warm notes, soft mallet or kalimba",
        "length": "0.6-0.9s"
    },
    "knock.mp3": {
        "q": "soft wood knock",
        "description": "Two soft taps on wood, polite knock",
        "length": "0.3-0.5s"
    },
    "ok.mp3": {
        "q": "success chime marimba",
        "description": "Rising 3-4 note arpeggio, glassy marimba or celesta",
        "length": "0.5-0.8s"
    },
    "info.mp3": {
        "q": "soft bell ping",
        "description": "One or two bell notes, quieter than ok",
        "length": "0.3-0.6s"
    },
    "hello.mp3": {
        "q": "cute robot chirp",
        "description": "Short rising boo-weep, friendly robot greeting",
        "length": "0.3-0.5s"
    },
    "boop.mp3": {
        "q": "soft pop UI",
        "description": "Soft squishy pop, rubbery bwip",
        "length": "0.1-0.2s"
    },
    "giggle.mp3": {
        "q": "cute robot laugh",
        "description": "Three rising chirps, cute robot laugh",
        "length": "0.3-0.5s"
    },
    "love.mp3": {
        "q": "magic sparkle chime",
        "description": "Shimmering upward celesta or wind-chime run",
        "length": "0.7-1.0s"
    },
    "grumble.mp3": {
        "q": "cartoon grumble soft",
        "description": "Two low wobbly descending notes, robot pouting",
        "length": "0.3-0.5s"
    },
    "beg.mp3": {
        "q": "cute question chirp",
        "description": "Two rising chirps with upward bend, pleading",
        "length": "0.4-0.6s"
    },
    "purr.mp3": {
        "q": "cat purr loop",
        "description": "Seamless cat purr loop, cleaned and low-passed",
        "length": "2-4s loop"
    },
    "chime.mp3": {
        "q": "single wind chime",
        "description": "Single bright aluminium chime with long decay",
        "length": "1-2s"
    },
    "bubble.mp3": {
        "q": "single bubble bloop",
        "description": "Round rising water bubble bloop",
        "length": "0.1-0.2s"
    }
}


def search_pixabay(api_key: str, query: str) -> list[dict]:
    """Search Pixabay for sounds."""
    url = f"https://pixabay.com/api/sounds/?key={api_key}&q={urllib.parse.quote(query)}&per_page=10"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode())
            return data.get("sounds", [])
    except Exception as e:
        print(f"Error searching Pixabay: {e}")
        return []


def download_sound(url: str, dest: Path) -> bool:
    """Download a sound file."""
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())
        return True
    except Exception as e:
        print(f"Error downloading: {e}")
        return False


def main() -> int:
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        print("Please set PIXABAY_API_KEY environment variable.")
        print("Get a free API key from: https://pixabay.com/api/docs/")
        return 1

    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

    print("Searching Pixabay for sounds...")
    print("-" * 60)

    for filename, info in SOUNDS.items():
        print(f"\n{filename}: {info['description']}")
        print(f"  Search: {info['q']}")

        dest = SOUNDS_DIR / filename
        if dest.exists():
            print(f"  Already exists, skipping.")
            continue

        sounds = search_pixabay(api_key, info["q"])
        if not sounds:
            print(f"  No results found.")
            continue

        # Show top 3 results
        for i, sound in enumerate(sounds[:3], 1):
            print(f"  {i}. {sound.get('name', 'Unknown')} by {sound.get('user', 'Unknown')}")
            print(f"     URL: {sound.get('url', 'N/A')}")

    print("\n" + "-" * 60)
    print("\nNote: This script shows search results but doesn't auto-download.")
    print("Please visit the URLs above, preview the sounds, and download manually.")
    print(f"\nPlace downloaded files in: {SOUNDS_DIR}")
    print("\nAlternative sources:")
    print("  - Freesound (CC0 only): https://freesound.org")
    print("  - Zapsplat: https://www.zapsplat.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
