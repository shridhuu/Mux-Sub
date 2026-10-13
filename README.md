# Mux-Sub

> Automated batch video, subtitle, font, and audio muxing pipeline powered by FFmpeg.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green.svg)](https://ffmpeg.org/)

**Mux-Sub** is a high-performance, automated FFmpeg muxing utility designed for anime, donghua, TV series, and multi-episode video releases. It intelligently matches video files with subtitles, extracts and embeds all required fonts, repairs stream start-delays, standardizes audio tracks (auto-generating complementary AAC/EAC3 tracks), adds chapters, and creates clean, standardized Matroska (`.mkv`) releases.

---

## Key Features

### 🎯 Smart Episode Matching
- **Regex-Based Detection:** Automatically identifies episode numbers from common conventions:
  - `S01E02`, `1x02`, `EP02`, `Episode 02`, `[02]`, `_02_`, or plain numbers.
- **Positional Fallback:** When filenames lack episode numbers, matches video and subtitle files 1-to-1 in sorted order.
- **Multi-Version Protection:** Differentiates multiple qualities/encodes of the same episode (e.g. `[1080p]` vs `[2160p]`) using bracketed quality tags to prevent file collisions.

### 🔊 Audio Track Intelligence & Dual-Codec Completeness
- **Proper Stream Labeling:** Reads channel layout and codec to build track titles (e.g., `AAC 2.0`, `EAC3 5.1`, `FLAC 2.0`, `Opus 2.0`).
- **Complementary Audio Generation:**
  - If a channel group has **AAC** but lacks **E-AC-3**, it automatically encodes an E-AC-3 track (640 kbps for 5.1/surround, 224 kbps for stereo).
  - If a channel group has **E-AC-3** but lacks **AAC**, it automatically encodes an AAC track (320 kbps for 5.1/surround, 192 kbps for stereo).
- **Zero Loss on Existing Tracks:** Existing video and audio streams are copied without re-encoding (`-c:v copy`, `-c:a copy`).

### ⏱️ Start-Delay Correction
- Probes container and stream timestamps (`start_time`).
- Detects A/V start delays between `0.001s` and `1.0s` that cause sync drift with external subtitles.
- Uses FFmpeg's `setts` bitstream filter (`PTS-X/TB`, `DTS-X/TB`) to shift timestamps back to `0.0s`.
- Re-probes and verifies the corrected file before final muxing; offsets larger than `1.0s` are safely left untouched.

### 🔤 Font Parsing & Automatic Embedding
- **Subtitle Script Inspection:** Parses `.ass` and `.ssa` subtitle files to extract font families defined in `[V4+ Styles]` and inline `\fn` override tags.
- **Missing Font Warnings:** Compares referenced fonts against available fonts in `sub/font/` and warns about missing typefaces prior to muxing.
- **Matroska Attachments:** Automatically embeds all font files (`.ttf`, `.otf`, `.ttc`) found in `sub/font/` with correct MIME types.

### 📑 Chapters & Jump Points
- Interactive prompt to add custom chapter markers (e.g., Opening, Title, Episode, Ending) using `Name|MM:SS` or `Name|HH:MM:SS`.
- Compiles metadata into standard Matroska chapters.

### 🛡️ Robust & Windows-Resilient
- **Atomic Operations & Lock Retries:** Includes retry loops (`safe_unlink`, `safe_replace`) to handle temporary file locks caused by Windows Explorer thumbnails, media players, or antivirus scanners.
- **Crash Pause Hook:** Keeps the console open if an error occurs so double-clicking the script in Windows never closes prematurely.
- **Deduplication:** Tags files with `muxed_by=@Shridhuu` metadata and skips previously muxed files unless re-muxing is explicitly forced.
- **HEVC / Dolby Vision Compatibility:** Applies `-tag:v hvc1` to avoid Matroska container rejection of MP4 `dvh1`/`dvhe` codec tags.
- **Stale Temp Cleanup:** Automatically removes orphaned `.mux_*.mkv` or `.fix_*.mkv` files from interrupted runs.

---

## Folder Structure

Place `main.py` directly in the folder alongside your source video files:

```text
Series_Folder/
├── main.py              # Mux-Sub script
├── Episode 01.mkv       # Video raw 1
├── Episode 02.mp4       # Video raw 2
├── Episode 03.mkv       # Video raw 3
└── sub/
    ├── Episode 01.ass   # Subtitle 1
    ├── Episode 02.srt   # Subtitle 2
    ├── Episode 03.ass   # Subtitle 3
    └── font/
        ├── Arial-Bold.ttf
        ├── CCWildWords.otf
        └── Trebuchet.ttc
```

---

## Requirements

- **Python 3.8+** (Standard library only — no `pip` dependencies needed)
- **FFmpeg & FFprobe** (Required on system `PATH`)

---

## Installing FFmpeg

FFmpeg and FFprobe must be installed and accessible from your command line (`PATH`).

### Windows

#### Method 1: Using Package Manager (Recommended)
Open PowerShell or Command Prompt and run one of the following:

```powershell
# Windows Package Manager (built into Windows 10/11)
winget install Gyan.FFmpeg

# Chocolatey
choco install ffmpeg

# Scoop
scoop install ffmpeg
```

#### Method 2: Manual Installation & Adding to PATH
1. Download `ffmpeg-release-essentials.zip` from [gyan.dev FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) or [official FFmpeg download page](https://ffmpeg.org/download.html).
2. Extract the archive (e.g., to `C:\ffmpeg`).
3. Add `C:\ffmpeg\bin` to your system `PATH`:
   - Press <kbd>Win</kbd> + <kbd>S</kbd>, search for **Environment Variables**, and select **Edit the system environment variables**.
   - Click **Environment Variables...** button.
   - Under **User variables** or **System variables**, select **Path** and click **Edit**.
   - Click **New**, enter `C:\ffmpeg\bin`, and click **OK**.
4. Restart your terminal or command prompt.

---

### macOS

Install via [Homebrew](https://brew.sh/):

```bash
brew install ffmpeg
```

---

### Linux

Install via package manager:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# Arch Linux
sudo pacman -S ffmpeg

# Fedora
sudo dnf install ffmpeg
```

---

### Verify Installation

Open a new terminal window and verify both tools are found:

```bash
ffmpeg -version
ffprobe -version
```

---

## Installation

Clone the repository:

```bash
git clone https://github.com/shridhuu/Mux-Sub.git
cd Mux-Sub
```

Or download [`main.py`](main.py) directly and place it in your target video folder.

---

## Usage

Open a terminal in the folder containing your videos and execute:

```bash
python main.py
```

### Interactive Prompts

When started, Mux-Sub asks a series of setup questions:

1. **Series Name (optional):**
   ```text
   Series Name (optional): Solo Leveling
   ```
   Leave blank to preserve the original video file name.

2. **Output Template (optional):**
   ```text
   Output Template (optional, blank = Series EPxx): {series} S02E{ep2} [1080p Dual-Audio]
   ```
   Supports dynamic format tags (see [Template Variables](#template-variables)).

3. **Add jump points? (y/n):**
   ```text
   Add jump points? (y/n): y
   Enter jump points as Name|MM:SS or Name|HH:MM:SS
   Leave blank to finish
   > Opening|01:30
   > Episode|03:00
   > Ending|22:15
   > 
   ```

4. **Force re-mux already-muxed files? (y/n):**
   Skip or re-process files previously tagged with `@Shridhuu`.

5. **Remove start delay from raws if present? (Y/n):**
   Enables automated probing and correction for start timestamp offsets.

### Execution Plan (Phase 1 & Phase 2)

Before executing any FFmpeg command, Mux-Sub displays a full execution plan:

```text
Found 12 font(s)
Matched 12 item(s)

Planned:
  [EP01] Raw_01.mp4 + Sub_01.ass -> Solo Leveling S02E01.mkv
         start delay found: video +0.080s, audio #1 +0.080s -> will be shifted to 0
  [EP02] Raw_02.mp4 + Sub_02.ass -> Solo Leveling S02E02.mkv

Warning: fonts referenced but not found in sub/font:
  Sub_01.ass: CCWildWords, HelveticaNeue-Bold

Proceed with muxing 12 file(s)? (y/n): y
```

---

## Template Variables

Customize output filenames with the following variables:

| Variable | Description | Example Output |
|---|---|---|
| `{series}` | Series name entered at prompt | `Frieren` |
| `{ep}` | Raw episode number | `1` |
| `{ep2}` | Two-digit zero-padded episode number | `01` |
| `{video_stem}` | Original raw video filename (without extension) | `[Raw]_Frieren_01` |

**Template Examples:**
- `{series} EP{ep2}` &rarr; `Frieren EP01.mkv`
- `{series} S01E{ep2} [HEVC EAC3]` &rarr; `Frieren S01E01 [HEVC EAC3].mkv`
- `{video_stem}_Muxed` &rarr; `[Raw]_Frieren_01_Muxed.mkv`

---

## Supported Formats

### Video Formats
| Format | Extension | Container Conversion |
|---|---|---|
| Matroska | `.mkv` | Lossless copy |
| MPEG-4 | `.mp4`, `.m4v` | Remuxed to `.mkv` |
| QuickTime | `.mov` | Remuxed to `.mkv` |
| AVI | `.avi` | Remuxed to `.mkv` |
| WebM | `.webm` | Remuxed to `.mkv` |

> Output is always a Matroska (`.mkv`) file.

### Subtitle Priority
When multiple subtitle formats exist for an episode, Mux-Sub prioritizes:
1. `.ass` (Advanced SubStation Alpha)
2. `.ssa` (SubStation Alpha)
3. `.srt` (SubRip)
4. `.vtt` (WebVTT)
5. `.sub` (MicroDVD / VobSub)

All text subtitles are muxed as standard `ass` tracks and tagged with `language=eng`.

### Font Formats
- `.ttf` (TrueType Font)
- `.otf` (OpenType Font)
- `.ttc` (TrueType Collection)

---

## Stream Handling Summary

| Stream | Action | Details |
|---|---|---|
| **Video** | Passthrough (`-c:v copy`) | HEVC streams tagged with `hvc1` for compatibility |
| **Original Audio** | Passthrough (`-c:a copy`) | Descriptive track titles added (`Codec Channels`) |
| **Complementary Audio** | Generated | Auto-encodes EAC3 (from AAC) or AAC (from EAC3) |
| **Subtitles** | Embedded (`-c:s ass`) | Tagged as default English track |
| **Fonts** | Attached (`-attach`) | Correct MIME types set for Matroska attachment |
| **Chapters** | Injected (`-map_chapters`) | Formatted from input jump points |

---

## Troubleshooting

### `ffmpeg/ffprobe not found`
Ensure FFmpeg is installed and added to your system environment variables. Test in terminal with:
```bash
ffmpeg -version
```

### Windows File Lock Errors
If an episode fails during final rename with `file locked`:
- Close any media players (VLC, MPC-HC, MPV) playing the file.
- Close Windows Explorer windows previewing the video.
- Muxed files are saved temporarily as `.mux_<filename>.mkv` and can be manually renamed once unlocked.

### Font Missing Warnings
If `main.py` reports fonts missing:
- Verify that font files are in the `sub/font/` directory.
- Check font filename or install the fonts into `sub/font/`.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Author

Created by **[@Shridhuu](https://github.com/shridhuu)**
