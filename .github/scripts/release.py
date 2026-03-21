#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SOUNDPACK_ROOTS = {"keyboard", "mouse"}
SOUNDPACK_DEPTH = 4
OUTPUTS_DIR = Path(".github/outputs")


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


def create_zip(soundpack_path, soundpack_uuid, soundpack_type, config):
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUTS_DIR / f"{soundpack_uuid}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(Path(soundpack_path).iterdir()):
            if file.is_file() and file.suffix != ".zip":
                if file.name == "config.json":
                    config["id"] = soundpack_uuid
                    config["metadata"]["category"] = soundpack_type
                    zf.writestr(file.name, json.dumps(config, indent=2) + "\n")
                else:
                    zf.write(file, file.name)
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
