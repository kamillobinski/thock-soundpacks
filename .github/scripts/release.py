#!/usr/bin/env python3
import json
import os
import statistics
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SOUNDPACK_ROOTS = {"keyboard", "mouse"}
SOUNDPACK_DEPTH = 4
OUTPUTS_DIR = Path(".github/outputs")
AUDIO_EXTENSIONS = {".mp3", ".wav"}
NORMALIZE_TARGET_DB = -6.0
CLIP_CEILING_DB = -0.1
LIMITER_ATTACK_MS = 5.0
LIMITER_RELEASE_MS = 50.0


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def is_soundpack_file(path):
    parts = Path(path).parts
    return len(parts) >= SOUNDPACK_DEPTH and parts[0] in SOUNDPACK_ROOTS


def soundpack_dir(path):
    return str(Path(*Path(path).parts[:SOUNDPACK_DEPTH]))


def allow_multiple():
    msg = run(["git", "log", "-1", "--format=%s"])
    return "[allow-multiple]" in msg


def get_changed_soundpacks():
    manual = os.environ.get("SOUNDPACK_PATHS", "").strip()
    if manual:
        paths = sorted(p.strip() for p in manual.split(",") if p.strip())
        for path in paths:
            print(f"Manual soundpack: {path}")
        return paths

    before = os.environ["BEFORE_SHA"]
    after = os.environ["AFTER_SHA"]
    changed = run(["git", "diff", "--name-only", before, after]).splitlines()
    soundpack_files = [f for f in changed if is_soundpack_file(f)]

    if not soundpack_files:
        print("No soundpack files changed, skipping release")
        sys.exit(0)

    dirs = {soundpack_dir(f) for f in soundpack_files}

    if len(dirs) != 1 and not allow_multiple():
        fail(f"Expected exactly one soundpack, found: {sorted(dirs)}")

    paths = sorted(dirs)
    for path in paths:
        print(f"Detected soundpack: {path}")
    return paths


def load_config(soundpack_path):
    return json.loads((Path(soundpack_path) / "config.json").read_text())


def load_manifest():
    run(["git", "fetch", "origin", "main"])
    raw = run(["git", "show", "origin/main:manifest.json"])
    if not raw:
        return {"soundpacks": {"keyboard": [], "mouse": []}}
    return json.loads(raw)


def get_or_create_uuid(manifest, soundpack_type, soundpack_path):
    for entry in manifest.get("soundpacks", {}).get(soundpack_type, []):
        if entry.get("content", {}).get("path") == soundpack_path:
            existing = entry["id"]
            print(f"Reusing existing UUID: {existing}")
            return existing
    new_id = str(uuid.uuid4())
    print(f"Generated new UUID: {new_id}")
    return new_id


def get_peak_db(file_path):
    result = subprocess.run(
        ["ffmpeg", "-i", str(file_path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True
    )
    for line in result.stderr.splitlines():
        if "max_volume" in line:
            return float(line.split("max_volume:")[1].strip().replace(" dB", ""))
    return None


def get_reference_filenames(config):
    sounds = config.get("sounds", {})
    if "default" in sounds:
        return set(sounds["default"].get("down", []))
    filenames = set()
    for group in sounds.values():
        for f in group.get("down", []):
            filenames.add(f)
    return filenames


def normalize_and_convert(soundpack_path, temp_dir, config):
    audio_files = [
        f for f in Path(soundpack_path).iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]

    peaks = {f: get_peak_db(f) for f in audio_files}
    peaks = {f: p for f, p in peaks.items() if p is not None}

    ref_filenames = get_reference_filenames(config)
    ref_peaks = [p for f, p in peaks.items() if f.name in ref_filenames]
    median_ref = statistics.median(ref_peaks) if ref_peaks else max(peaks.values(), default=0.0)

    gain_db = NORMALIZE_TARGET_DB - median_ref
    gain_linear = 10 ** (gain_db / 20)
    clip_ceiling_linear = 10 ** (CLIP_CEILING_DB / 20)

    print(f"Limiter + normalization: median ref={median_ref:.1f}dB, gain={gain_db:+.1f}dB")

    audio_filter = (
        f"alimiter=level_in={gain_linear:.6f}"
        f":level_out=1"
        f":limit={clip_ceiling_linear:.6f}"
        f":attack={LIMITER_ATTACK_MS}"
        f":release={LIMITER_RELEASE_MS}"
        f":level=0"
    )
    mapping = {}
    for f in audio_files:
        wav_name = f.stem + ".wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(f), "-af", audio_filter,
             str(Path(temp_dir) / wav_name)],
            check=True, capture_output=True
        )
        mapping[f.name] = wav_name

    return mapping


def remap_config_audio(config, mapping):
    for key_group in config.get("sounds", {}).values():
        for direction in ("down", "up"):
            if direction in key_group:
                key_group[direction] = [mapping.get(f, f) for f in key_group[direction]]


def create_zip(soundpack_path, soundpack_uuid, soundpack_type, config):
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUTS_DIR / f"{soundpack_uuid}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        mapping = normalize_and_convert(soundpack_path, tmp, config)
        config["id"] = soundpack_uuid
        config["metadata"]["category"] = soundpack_type
        remap_config_audio(config, mapping)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("config.json", json.dumps(config, indent=2) + "\n")
            for wav_path in sorted(Path(tmp).iterdir()):
                if wav_path.is_file():
                    zf.write(wav_path, wav_path.name)
            license_path = Path(soundpack_path) / "LICENSE"
            if license_path.is_file():
                zf.write(license_path, "LICENSE")

    size = zip_path.stat().st_size
    print(f"Created zip: {size} bytes")
    return size


def build_manifest_entry(soundpack_uuid, config, soundpack_path, zip_size):
    repo = os.environ.get("GITHUB_REPOSITORY", "kamillobinski/thock-soundpacks")
    meta = config["metadata"]
    return {
        "id": soundpack_uuid,
        "metadata": {
            "name": meta["name"],
            "brand": meta["brand"],
            "author": meta["author"],
            "supportsKeyUp": meta["supportsKeyUp"],
            "category": meta["category"],
        },
        "content": {
            "path": soundpack_path,
        },
        "download": {
            "url": f"https://github.com/{repo}/raw/refs/heads/main/{soundpack_path}/{soundpack_uuid}.zip",
            "size": zip_size,
        },
        "license": {
            "type": config["license"]["type"],
            "url": config["license"]["url"],
        },
    }


def update_manifest(manifest, soundpack_type, soundpack_path, entry):
    entries = manifest.setdefault("soundpacks", {}).setdefault(soundpack_type, [])
    for i, e in enumerate(entries):
        if e.get("content", {}).get("path") == soundpack_path:
            entries[i] = entry
            return
    entries.append(entry)


def write_outputs(releases, manifest):
    manifest["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (OUTPUTS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUTPUTS_DIR / "releases.json").write_text(json.dumps(releases, indent=2) + "\n")

    apply_lines = ["#!/bin/bash"]
    for r in releases:
        path = r["path"]
        uid = r["uuid"]
        apply_lines.append(f'mkdir -p "{path}"')
        apply_lines.append(f'cp ".github/outputs/{uid}.zip" "{path}/{uid}.zip"')
        apply_lines.append(f'echo "{path}/{uid}.zip"')
    (OUTPUTS_DIR / "apply.sh").write_text("\n".join(apply_lines) + "\n")


def main():
    soundpack_paths = get_changed_soundpacks()
    manifest = load_manifest()

    releases = []
    for soundpack_path in soundpack_paths:
        config = load_config(soundpack_path)
        soundpack_type = Path(soundpack_path).parts[0]
        soundpack_uuid = get_or_create_uuid(manifest, soundpack_type, soundpack_path)

        zip_size = create_zip(soundpack_path, soundpack_uuid, soundpack_type, config)
        entry = build_manifest_entry(soundpack_uuid, config, soundpack_path, zip_size)
        update_manifest(manifest, soundpack_type, soundpack_path, entry)

        releases.append({"uuid": soundpack_uuid, "path": soundpack_path})

    write_outputs(releases, manifest)


if __name__ == "__main__":
    main()
