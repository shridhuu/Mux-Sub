from pathlib import Path
import json
import subprocess
import re
import sys
import time

VIDEO_DIR = Path(".")
SUB_DIR = VIDEO_DIR / "sub"
FONT_DIR = SUB_DIR / "font"

SUB_EXTENSIONS = {".ass", ".ssa", ".srt", ".vtt", ".sub"}
SUB_PREFERENCE = {".ass": 0, ".ssa": 1, ".srt": 2, ".vtt": 3, ".sub": 4}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm"}
MUX_TAG = "@Shridhuu"

# Start-delay fix: a stream whose start_time is above DELAY_TOLERANCE is
# shifted back to 0. Offsets above MAX_FIX_DELAY are assumed to be
# intentional (shifting them would visibly break A/V sync) and left alone.
DELAY_TOLERANCE = 0.001   # seconds
MAX_FIX_DELAY = 1.0       # seconds
VERIFY_TOLERANCE = 0.002  # seconds, allowed leftover after the fix


def extract_episode(name):
    patterns = [
        r"(?i)s\d+e(\d+)",
        r"(?i)(\d+)x(\d+)",
        r"(?i)(?:ep|episode)[\s._-]*(\d+)",
        r"(?i)(?:^|[\s._\-\[\(])e(\d{1,3})(?:$|[\s._\-\]\)])",
        r"(?i)(?:^|[\s._-])(\d{1,3})(?:$|[\s._-])",
    ]
    for pattern in patterns:
        m = re.search(pattern, name)
        if m:
            if len(m.groups()) >= 2 and m.group(2):
                return int(m.group(2))
            if m.group(1):
                return int(m.group(1))
    nums = re.findall(r"(?<!\d)(\d{1,3})(?!\d)", name)
    if nums:
        return int(nums[-1])
    return None


def extract_quality_tag(stem):
    """Pull the last [bracketed] segment (usually res/codec info) for
    differentiating multiple video versions of the same episode."""
    tags = re.findall(r"\[([^\[\]]*)\]", stem)
    return tags[-1] if tags else None


def get_ass_fonts(sub_path):
    """Collect font names referenced by an .ass/.ssa file: both the
    Fontname column in [V4+ Styles] and any inline \\fn override tags."""
    fonts_used = set()
    try:
        content = sub_path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return fonts_used

    in_styles = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("[") :
            in_styles = "styles]" in stripped.lower()
            continue
        if in_styles and stripped.lower().startswith("style:"):
            parts = stripped.split(":", 1)[1].split(",")
            if len(parts) > 1 and parts[1].strip():
                fonts_used.add(parts[1].strip())

    for m in re.finditer(r"\\fn([^\\}]+)", content):
        name = m.group(1).strip()
        if name:
            fonts_used.add(name)

    return fonts_used


def parse_time_to_ms(text):
    parts = text.strip().split(":")
    if len(parts) == 2:
        h = 0
        m, s = parts
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError("Bad time format")
    if "." in s:
        sec, frac = s.split(".", 1)
        frac = (frac + "000")[:3]
    else:
        sec = s
        frac = "000"
    return ((int(h) * 3600) + (int(m) * 60) + int(sec)) * 1000 + int(frac)


def is_already_muxed(path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format_tags=muxed_by",
        "-of", "default=nk=1:nw=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout.strip() == MUX_TAG
    except Exception:
        return False


def get_video_codec(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=nk=1:nw=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout.strip()
    except Exception:
        return ""


def get_audio_streams(path):
    """Return [(codec_name, channels), ...] for each audio stream, in
    file order (index i here matches ffmpeg's 0:a:i specifier)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_name,channels",
        "-of", "csv=p=0",
        str(path),
    ]
    streams = []
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split(",")
            codec = parts[0].strip() if parts else ""
            channels = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 2
            streams.append((codec, channels))
    except Exception:
        return []
    return streams


def audio_label(codec, channels):
    """Build a proper track title like 'AAC 2.0' or 'EAC3 5.1'."""
    codec_label = {
        "aac": "AAC", "eac3": "EAC3", "ac3": "AC3",
        "flac": "FLAC", "opus": "Opus", "dts": "DTS",
    }.get(codec, codec.upper())
    channel_label = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(channels, f"{channels}ch")
    return f"{codec_label} {channel_label}"


def get_stream_starts(path):
    """Return [(kind, type_index, start_seconds), ...] for the first video
    stream and every audio stream, in file order. type_index matches
    ffmpeg's v:N / a:N specifiers. start_seconds is None when ffprobe can't
    report one. Returns None if ffprobe itself failed.

    This is the same data as:
      ffprobe -show_entries stream=index,codec_type,start_time"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=index,codec_type,start_time",
        "-of", "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        data = json.loads(result.stdout or "{}")
    except Exception:
        return None

    counters = {"video": 0, "audio": 0}
    found = []
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind not in counters:
            continue
        type_index = counters[kind]
        counters[kind] += 1
        if kind == "video" and type_index > 0:
            continue  # only v:0 is ever muxed
        try:
            start = float(stream.get("start_time"))
        except (TypeError, ValueError):
            start = None
        found.append((kind, type_index, start))
    return found


def plan_delay_fix(path):
    """Decide which streams of `path` need their start shifted to 0.

    Returns (shifts, skip_reason). `shifts` is a list of
    (kind, type_index, start_seconds); empty means nothing to do.
    `skip_reason` is set when a delay exists but is too large to be safe to
    auto-remove."""
    streams = get_stream_starts(path)
    if not streams:
        return [], None
    delayed = [(k, i, s) for k, i, s in streams if s is not None and s > DELAY_TOLERANCE]
    if not delayed:
        return [], None
    biggest = max(s for _, _, s in delayed)
    if biggest > MAX_FIX_DELAY:
        return [], (
            f"start offset of {biggest:.3f}s is over {MAX_FIX_DELAY:.1f}s - "
            f"looks intentional, not touching it"
        )
    return delayed, None


def describe_shifts(shifts):
    parts = []
    for kind, idx, start in shifts:
        name = "video" if kind == "video" else f"audio #{idx + 1}"
        parts.append(f"{name} +{start:.3f}s")
    return ", ".join(parts)


def run_delay_fix(src, dest, shifts, label):
    """Stream-copy src -> dest with every stream in `shifts` moved back to
    start_time 0 (ffmpeg -copyts + the setts bitstream filter), then
    re-probe dest to confirm it worked. Returns True only if dest exists
    and verifies clean; on any problem dest is removed and False returned.

    Equivalent to:
      ffmpeg -copyts -i src -map 0 -c copy \\
             -bsf:v:0 "setts=pts=PTS-X/TB:dts=DTS-X/TB" \\
             -avoid_negative_ts disabled dest
    except X is each stream's own probed start_time, not a fixed number."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-copyts", "-i", str(src),
        # Same streams the real mux below keeps, in the same order.
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy",
    ]
    if get_video_codec(src) == "hevc":
        # Same reason as in the main mux: Matroska rejects the dvh1/dvhe
        # tag some HEVC sources carry, so force a standard tag.
        cmd += ["-tag:v", "hvc1"]
    for kind, idx, start in shifts:
        spec = "v" if kind == "video" else "a"
        cmd += [
            f"-bsf:{spec}:{idx}",
            f"setts=pts=PTS-{start:.6f}/TB:dts=DTS-{start:.6f}/TB",
        ]
    cmd += ["-avoid_negative_ts", "disabled", str(dest)]

    def discard():
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"{label} Delay fix failed - original left untouched")
        if e.stderr:
            print("  ffmpeg error:")
            for line in e.stderr.strip().splitlines()[-15:]:
                print(f"    {line}")
        discard()
        return False
    except FileNotFoundError:
        print(f"{label} Failed - ffmpeg/ffprobe not found. Check it's installed and on PATH.")
        discard()
        return False

    # Re-verify: every stream we shifted should now start at ~0.
    after = get_stream_starts(dest)
    if after is None:
        print(f"{label} Delay fix couldn't be verified (ffprobe failed) - original left untouched")
        discard()
        return False
    after_map = {(k, i): s for k, i, s in after}
    leftovers = []
    for kind, idx, _ in shifts:
        s = after_map.get((kind, idx))
        if s is None or abs(s) > VERIFY_TOLERANCE:
            leftovers.append((kind, idx, s if s is not None else float("nan")))
    if leftovers:
        print(f"{label} Delay still present after fix ({describe_shifts(leftovers)}) - original left untouched")
        discard()
        return False

    return True


def safe_unlink(path, retries=5, delay=1.0):
    """Delete a file, retrying if it's briefly locked (Explorer thumbnails,
    antivirus scans, media players holding a handle open on Windows)."""
    last_err = None
    for _ in range(retries):
        try:
            path.unlink()
            return
        except OSError as e:
            last_err = e
            time.sleep(delay)
    raise last_err


def safe_replace(src, dest, retries=5, delay=1.0):
    """Same retry behavior as safe_unlink, for renaming into place."""
    last_err = None
    for _ in range(retries):
        try:
            src.replace(dest)
            return
        except OSError as e:
            last_err = e
            time.sleep(delay)
    raise last_err


def pause_exit(code=0):
    """Exit but keep the console window open, so double-clicking the
    script doesn't just flash and vanish before you can read the output."""
    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass
    sys.exit(code)


def _crash_pause(exc_type, exc_value, exc_tb):
    import traceback
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        input("\nScript crashed - press Enter to close...")
    except EOFError:
        pass


sys.excepthook = _crash_pause


series_name = input("Series Name (optional): ").strip()
output_template = input("Output Template (optional, blank = Series EPxx): ").strip()
add_jump_points = input("Add jump points? (y/n): ").strip().lower().startswith("y")
force_remux = input("Force re-mux already-muxed files? (y/n): ").strip().lower().startswith("y")
fix_delay = not input("Remove start delay from raws if present? (Y/n): ").strip().lower().startswith("n")

stale_temp = sorted([*VIDEO_DIR.glob(".mux_*.mkv"), *VIDEO_DIR.glob(".fix_*.mkv")])
if stale_temp:
    print(f"\nFound {len(stale_temp)} leftover temp file(s) from a previous interrupted run - removing:")
    for f in stale_temp:
        print(f"  {f.name}")
        try:
            safe_unlink(f, retries=1)
        except OSError as e:
            print(f"    couldn't remove ({e}) - remove it manually before re-running")
    print()

chapter_file = None

if add_jump_points:
    print("Enter jump points as Name|MM:SS or Name|HH:MM:SS")
    print("Leave blank to finish")
    entries = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        if "|" not in line:
            print("Use Name|MM:SS")
            continue
        title, time_text = line.split("|", 1)
        title = title.strip()
        time_text = time_text.strip()
        try:
            start_ms = parse_time_to_ms(time_text)
            entries.append((title, start_ms))
        except ValueError:
            print("Bad time format")
    if entries:
        chapter_file = VIDEO_DIR / ".jump_points.ffmeta"
        text = ";FFMETADATA1\n"
        for i, (title, start_ms) in enumerate(entries):
            end_ms = entries[i + 1][1] if i + 1 < len(entries) else 999999999
            text += (
                "\n[CHAPTER]\n"
                "TIMEBASE=1/1000\n"
                f"START={start_ms}\n"
                f"END={end_ms}\n"
                f"title={title}\n"
            )
        chapter_file.write_text(text, encoding="utf-8")

fonts = []
if FONT_DIR.exists():
    fonts = sorted(
        [f for f in FONT_DIR.iterdir() if f.is_file() and f.suffix.lower() in {".ttf", ".otf", ".ttc"}],
        key=lambda p: p.name.lower()
    )

video_files = sorted(
    [
        p for p in VIDEO_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and p.name != "test.mkv"
        and not p.name.startswith("temp_")
        and not p.name.startswith(".mux_")
        and not p.name.startswith(".fix_")
    ],
    key=lambda p: p.name.lower()
)

subtitle_files = sorted(
    [p for p in SUB_DIR.rglob("*") if p.is_file() and p.suffix.lower() in SUB_EXTENSIONS],
    key=lambda p: (SUB_PREFERENCE.get(p.suffix.lower(), 99), p.name.lower())
)

if not video_files:
    print("No video files found.")
    if chapter_file and chapter_file.exists():
        chapter_file.unlink()
    pause_exit(0)

# video_by_ep now maps ep -> LIST of videos, so every quality/codec version
# of an episode gets picked up, not just the first one found.
video_by_ep = {}
video_order = []
for video in video_files:
    if not force_remux and is_already_muxed(video):
        continue
    ep = extract_episode(video.stem)
    video_order.append((video, ep))
    if ep is not None:
        video_by_ep.setdefault(ep, []).append(video)

subtitle_by_ep = {}
subtitle_order = []
for sub in subtitle_files:
    ep = extract_episode(sub.stem)
    subtitle_order.append((sub, ep))
    if ep is not None:
        current = subtitle_by_ep.get(ep)
        if current is None:
            subtitle_by_ep[ep] = sub
        else:
            current_rank = (SUB_PREFERENCE.get(current.suffix.lower(), 99), current.name.lower())
            new_rank = (SUB_PREFERENCE.get(sub.suffix.lower(), 99), sub.name.lower())
            if new_rank < current_rank:
                subtitle_by_ep[ep] = sub

paired = []
used_videos = set()
used_subs = set()

for ep in sorted(set(video_by_ep) & set(subtitle_by_ep)):
    s = subtitle_by_ep[ep]
    for v in video_by_ep[ep]:
        paired.append((v, s, ep))
        used_videos.add(v)
    used_subs.add(s)

remaining_videos = [v for v, ep in video_order if v not in used_videos]
remaining_subs = [s for s, ep in subtitle_order if s not in used_subs]

if not paired and len(remaining_videos) == len(remaining_subs) and remaining_videos:
    remaining_videos = sorted(remaining_videos, key=lambda p: p.name.lower())
    remaining_subs = sorted(
        remaining_subs,
        key=lambda p: (SUB_PREFERENCE.get(p.suffix.lower(), 99), p.name.lower())
    )
    for i, video in enumerate(remaining_videos):
        subtitle = remaining_subs[i]
        ep = i + 1
        paired.append((video, subtitle, ep))

paired = sorted(paired, key=lambda x: (x[2], x[0].name.lower()))

if not paired:
    print("No matching episodes found.")
    if chapter_file and chapter_file.exists():
        chapter_file.unlink()
    pause_exit(0)

print(f"Found {len(fonts)} font(s)")
for font in fonts:
    print(font.name)

print(f"Matched {len(paired)} item(s)")

# --- Phase 1: build the plan (names, font checks) without touching ffmpeg ---
available_font_names = {f.stem.lower() for f in fonts}
used_output_names = set()
font_warnings = {}
delay_skips = {}
jobs = []

for video, subtitle, ep in paired:
    if output_template:
        out_name = output_template.format(
            series=series_name,
            ep=ep,
            ep2=f"{ep:02d}",
            video_stem=video.stem
        ).strip()
    elif series_name:
        out_name = f"{series_name} EP{ep:02d}"
    else:
        out_name = video.stem

    if not out_name.lower().endswith(".mkv"):
        out_name += ".mkv"

    # Avoid overwriting when multiple video versions share the same episode.
    if out_name.lower() in used_output_names:
        base = out_name[:-4]
        tag = extract_quality_tag(video.stem)
        candidate = f"{base} [{tag}].mkv" if tag else out_name
        n = 2
        while candidate.lower() in used_output_names:
            candidate = f"{base} ({n}).mkv"
            n += 1
        out_name = candidate

    used_output_names.add(out_name.lower())

    if subtitle.suffix.lower() in {".ass", ".ssa"}:
        missing = sorted(
            f for f in get_ass_fonts(subtitle)
            if f.lower() not in available_font_names
        )
        if missing:
            font_warnings[subtitle.name] = missing

    shifts = []
    if fix_delay:
        shifts, skip_reason = plan_delay_fix(video)
        if skip_reason:
            delay_skips[video.name] = skip_reason

    jobs.append((video, subtitle, ep, out_name, shifts))

print("\nPlanned:")
for video, subtitle, ep, out_name, shifts in jobs:
    print(f"  [EP{ep:02d}] {video.name} + {subtitle.name} -> {out_name}")
    if shifts:
        print(f"         start delay found: {describe_shifts(shifts)} -> will be shifted to 0")

if font_warnings:
    print("\nWarning: fonts referenced but not found in sub/font:")
    for sub_name, missing in font_warnings.items():
        print(f"  {sub_name}: {', '.join(missing)}")

if delay_skips:
    print("\nWarning: start delay NOT removed for:")
    for vid_name, reason in delay_skips.items():
        print(f"  {vid_name}: {reason}")

confirm = input(f"\nProceed with muxing {len(jobs)} file(s)? (y/n): ").strip().lower().startswith("y")
if not confirm:
    print("Aborted.")
    if chapter_file and chapter_file.exists():
        chapter_file.unlink()
    pause_exit(0)

# --- Phase 2: actually mux ---
print()
for video, subtitle, ep, out_name, shifts in jobs:
    final_output = VIDEO_DIR / out_name
    temp_output = VIDEO_DIR / f".mux_{video.stem}.mkv"
    fix_temp = VIDEO_DIR / f".fix_{video.stem}.mkv"

    credit = f"Subtitled by {MUX_TAG}"

    # Step 0 (only when a delay was found): stream-copy the raw into a
    # temp file with the delayed streams moved back to start 0, verify it,
    # and then mux from that instead. On any failure the original raw is
    # left untouched and this episode is skipped.
    mux_input = video
    if shifts:
        print(f"[EP{ep:02d}] Removing start delay ({describe_shifts(shifts)})")
        if not run_delay_fix(video, fix_temp, shifts, f"[EP{ep:02d}]"):
            continue
        print(f"[EP{ep:02d}] Delay removed and verified")
        mux_input = fix_temp

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-i", str(mux_input),
        "-i", str(subtitle),
    ]

    if chapter_file is not None:
        cmd += ["-i", str(chapter_file)]

    audio_streams = get_audio_streams(mux_input)

    # Group existing aac/eac3 streams by channel count, so e.g. stereo and
    # 5.1 are tracked separately - a 5.1 EAC3 track doesn't count as
    # covering a missing stereo EAC3, and vice versa.
    by_channels = {}
    for i, (codec, channels) in enumerate(audio_streams):
        if codec in ("aac", "eac3"):
            by_channels.setdefault(channels, {}).setdefault(codec, i)

    audio_map_args = []
    audio_codec_args = []
    out_a = 0

    # Keep every existing stream, but always give it a proper title.
    for i, (codec, channels) in enumerate(audio_streams):
        audio_map_args += ["-map", f"0:a:{i}"]
        audio_codec_args += [
            f"-c:a:{out_a}", "copy",
            f"-metadata:s:a:{out_a}", f"title={audio_label(codec, channels)}",
        ]
        out_a += 1

    # Then, for each channel-count group, add whichever of AAC/EAC3 is
    # missing - generated from whichever version of that group already
    # exists. A group with both already present is left untouched.
    for channels, codecs_present in by_channels.items():
        if "aac" in codecs_present and "eac3" not in codecs_present:
            src_idx = codecs_present["aac"]
            bitrate = "640k" if channels > 2 else "224k"
            audio_map_args += ["-map", f"0:a:{src_idx}"]
            audio_codec_args += [
                f"-c:a:{out_a}", "eac3",
                f"-b:a:{out_a}", bitrate,
                f"-metadata:s:a:{out_a}", f"title={audio_label('eac3', channels)}",
                f"-disposition:a:{out_a}", "0",
            ]
            out_a += 1
        elif "eac3" in codecs_present and "aac" not in codecs_present:
            src_idx = codecs_present["eac3"]
            bitrate = "320k" if channels > 2 else "192k"
            audio_map_args += ["-map", f"0:a:{src_idx}"]
            audio_codec_args += [
                f"-c:a:{out_a}", "aac",
                f"-b:a:{out_a}", bitrate,
                f"-metadata:s:a:{out_a}", f"title={audio_label('aac', channels)}",
                f"-disposition:a:{out_a}", "0",
            ]
            out_a += 1

    if not audio_streams:
        # ffprobe couldn't read audio streams (or there are none) - fall
        # back to a plain passthrough rather than silently dropping audio.
        audio_map_args = ["-map", "0:a?"]
        audio_codec_args = ["-c:a", "copy"]

    cmd += [
        "-map", "0:v:0",
    ] + audio_map_args + [
        "-map", "1:0",
        "-map_metadata", "0",
    ]

    if chapter_file is not None:
        cmd += ["-map_chapters", "2"]

    cmd += [
        "-c:v", "copy",
    ]
    if get_video_codec(mux_input) == "hevc":
        # Matroska rejects the dvh1/dvhe tag some HEVC+Dolby Vision
        # sources carry from their MP4 container - force a standard tag.
        cmd += ["-tag:v", "hvc1"]
    cmd += audio_codec_args + [
        "-c:s", "ass",
        "-disposition:s:0", "default",
        "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:0", f"title=Subtitled by {MUX_TAG}",
        "-metadata", f"muxed_by={MUX_TAG}",
        "-metadata", f"comment={credit}",
        "-metadata", f"title={credit}",
    ]

    for idx, font in enumerate(fonts):
        mime = "application/vnd.ms-opentype" if font.suffix.lower() == ".otf" else "application/x-truetype-font"
        cmd += [
            "-attach", str(font),
            f"-metadata:s:t:{idx}", f"mimetype={mime}",
            f"-metadata:s:t:{idx}", f"filename={font.name}",
        ]

    cmd.append(str(temp_output))

    print(f"[EP{ep:02d}] {video.name} + {subtitle.name} -> {out_name}")

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        if final_output.exists() and final_output.resolve() != video.resolve():
            safe_unlink(final_output)

        if temp_output.exists():
            try:
                safe_unlink(video)
                safe_replace(temp_output, final_output)
            except OSError as file_err:
                print(f"[EP{ep:02d}] Mux succeeded, but couldn't finalize (file locked?): {file_err}")
                print(f"    Muxed output is sitting at: {temp_output.name} - rename it manually, or re-run once the lock clears.")
                continue

        print(f"[EP{ep:02d}] Done")
    except subprocess.CalledProcessError as e:
        print(f"[EP{ep:02d}] Failed")
        if e.stderr:
            print("  ffmpeg error:")
            for line in e.stderr.strip().splitlines()[-15:]:
                print(f"    {line}")
        if temp_output.exists():
            temp_output.unlink()
    except FileNotFoundError:
        print(f"[EP{ep:02d}] Failed - ffmpeg/ffprobe not found. Check it's installed and on PATH.")
        if temp_output.exists():
            temp_output.unlink()
    finally:
        if fix_temp.exists():
            try:
                safe_unlink(fix_temp)
            except OSError as e:
                print(f"[EP{ep:02d}] Couldn't remove temp file {fix_temp.name} ({e}) - delete it manually")

if chapter_file and chapter_file.exists():
    chapter_file.unlink()

print("Done.")
pause_exit(0)