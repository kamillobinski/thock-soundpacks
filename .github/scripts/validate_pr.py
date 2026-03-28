#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

SOUNDPACK_ROOTS = {"keyboard", "mouse"}
SOUNDPACK_DEPTH = 4
AUDIO_EXTENSIONS = {".mp3", ".wav"}
REQUIRED_TOP_LEVEL = {"metadata", "license", "sounds"}
REQUIRED_METADATA = {"name", "brand", "author", "supportsKeyUp"}
REQUIRED_LICENSE = {"type", "url"}


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def get_changed_files():
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]
    return run(["git", "diff", "--name-only", base_sha, head_sha]).splitlines()


def is_soundpack_file(path):
    parts = Path(path).parts
    return len(parts) >= SOUNDPACK_DEPTH and parts[0] in SOUNDPACK_ROOTS


def soundpack_dir(path):
    return str(Path(*Path(path).parts[:SOUNDPACK_DEPTH]))


def resolve_soundpack_path(changed_files):
    soundpack_files = [f for f in changed_files if is_soundpack_file(f)]
    other_files = [f for f in changed_files if not is_soundpack_file(f)]

    if not soundpack_files:
        print("Skipping validation, no changes made in soundpack files")
        sys.exit(0)

    if other_files:
        fail(f"PR must only contain soundpack files. Found non-soundpack files: {other_files}")

    soundpack_dirs = {soundpack_dir(f) for f in soundpack_files}
    if len(soundpack_dirs) > 1:
        fail(f"PR must contain exactly one soundpack. Found: {sorted(soundpack_dirs)}")

    path = soundpack_dirs.pop()
    print(f"Detected soundpack: {path}")
    return path


def load_config(soundpack_path):
    config_path = Path(soundpack_path) / "config.json"
    if not config_path.exists():
        fail(f"Missing config.json in {soundpack_path}")
    try:
        return json.loads(config_path.read_text())
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON in {config_path}: {e}")


def validate_config_schema(config):
    missing_top = REQUIRED_TOP_LEVEL - config.keys()
    if missing_top:
        fail(f"config.json missing required top-level keys: {sorted(missing_top)}")

    metadata = config["metadata"]
    missing_meta = REQUIRED_METADATA - metadata.keys()
    if missing_meta:
        fail(f"config.json metadata missing required keys: {sorted(missing_meta)}")

    missing_license = REQUIRED_LICENSE - config["license"].keys()
    if missing_license:
        fail(f"config.json license missing required keys: {sorted(missing_license)}")

    if not isinstance(metadata["supportsKeyUp"], bool):
        fail("config.json metadata.supportsKeyUp must be a boolean")


def validate_unique_audio_stems(soundpack_path):
    stems = {}
    for f in Path(soundpack_path).iterdir():
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            if f.stem in stems:
                fail(f"Duplicate audio stem '{f.stem}': {stems[f.stem]} and {f.name}")
            stems[f.stem] = f.name


def validate_audio_files(soundpack_path, config):
    missing = []
    for entry in config["sounds"].values():
        for direction in ("down", "up"):
            for filename in entry.get(direction, []):
                if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
                    fail(f"Unsupported audio extension for {filename} (supported: {AUDIO_EXTENSIONS})")
                if not (Path(soundpack_path) / filename).exists():
                    missing.append(filename)
    if missing:
        fail(f"Missing audio files referenced in config.json: {missing}")


def main():
    changed_files = get_changed_files()
    soundpack_path = resolve_soundpack_path(changed_files)
    config = load_config(soundpack_path)
    validate_config_schema(config)
    validate_unique_audio_stems(soundpack_path)
    validate_audio_files(soundpack_path, config)
    print("All checks passed")


if __name__ == "__main__":
    main()
