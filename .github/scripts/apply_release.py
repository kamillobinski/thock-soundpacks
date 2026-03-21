#!/usr/bin/env python3
import json
import os
import shutil
from pathlib import Path

OUTPUTS_DIR = Path(".github/outputs")

releases = json.loads((OUTPUTS_DIR / "releases.json").read_text())

for r in releases:
    path = r["path"]
    uid = r["uuid"]
    os.makedirs(path, exist_ok=True)
    shutil.copy(OUTPUTS_DIR / f"{uid}.zip", f"{path}/{uid}.zip")
    print(f"{path}/{uid}.zip")
