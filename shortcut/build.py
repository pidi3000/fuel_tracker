#!/usr/bin/env python3
"""Builds "Fuel Tracker.shortcut" from fuel-tracker.cherri.

Needs the Cherri compiler (https://cherrilang.org, `brew install electrikmilk/cherri/cherri`).
The result is not signed, so the compiler never sends it anywhere; sign it before importing it
on an iPhone, see docs/shortcut.md.

Cherri sends every variable in a JSON body as text, so a decimal comma (locale!) would reach the
server as "45,5". This script turns those values into real JSON numbers.
"""

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "fuel-tracker.cherri"
OUTPUT = HERE / "Fuel Tracker.shortcut"

# The fields of POST /api/fuel-ups that are numbers
NUMBERS = {"vehicle_id", "odometer", "latitude", "longitude", "quantity", "total_price"}
NUMBER_ITEM = 3  # a dictionary item of type "number" (0 is text)


def compile_source() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(SOURCE, tmp)
        # --skip-sign: otherwise Cherri signs with a remote service when it is not on macOS
        subprocess.run(["cherri", SOURCE.name, "--skip-sign", "--no-ansi"], cwd=tmp, check=True)
        (compiled,) = Path(tmp).glob("*_unsigned.shortcut")
        return plistlib.loads(compiled.read_bytes())


def make_numbers(shortcut: dict) -> None:
    found = set()
    for action in shortcut["WFWorkflowActions"]:
        params = action.get("WFWorkflowActionParameters", {})
        if params.get("WFHTTPBodyType") != "JSON":
            continue
        for item in params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]:
            key = item["WFKey"]["Value"]["string"]
            if key in NUMBERS:
                item["WFItemType"] = NUMBER_ITEM
                found.add(key)
    if found != NUMBERS:
        sys.exit(f"The source does not send these numbers: {sorted(NUMBERS - found)}")


def main() -> None:
    shortcut = compile_source()
    make_numbers(shortcut)
    OUTPUT.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY))
    print(f"Wrote {OUTPUT.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
