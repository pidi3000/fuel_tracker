#!/usr/bin/env python3
"""Builds "Fuel Tracker.shortcut" from fuel-tracker.cherri.

Needs the Cherri compiler (https://cherrilang.org, `brew install electrikmilk/cherri/cherri`).
The result is not signed, so the compiler never sends it anywhere; sign it before importing it
on an iPhone, see docs/shortcut.md.

Afterwards this script makes two changes to what Cherri produced:

- Cherri sends every variable in a JSON body as text, so a decimal comma (locale!) would reach
  the server as "45,5". The script turns those values into real JSON numbers.
- Cherri puts a "Nothing" action between blocks, which show up as gray boxes in the Shortcuts
  app. They are not needed, except to start the list of vehicle names empty, so the script
  removes the others.
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
ACTION = "WFWorkflowActionIdentifier"
PARAMETERS = "WFWorkflowActionParameters"


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
        params = action.get(PARAMETERS, {})
        if params.get("WFHTTPBodyType") != "JSON":
            continue
        for item in params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]:
            key = item["WFKey"]["Value"]["string"]
            if key in NUMBERS:
                item["WFItemType"] = NUMBER_ITEM
                found.add(key)
    if found != NUMBERS:
        sys.exit(f"The source does not send these numbers: {sorted(NUMBERS - found)}")


def is_nothing(action: dict) -> bool:
    return action[ACTION] == "is.workflow.actions.nothing"


def remove_nothing(shortcut: dict) -> None:
    actions = shortcut["WFWorkflowActions"]
    kept, new_index = [], {}
    for i, action in enumerate(actions):
        # "Set Variable" without an input takes the one before it: Nothing, an empty list
        next_action = actions[i + 1] if i + 1 < len(actions) else None
        starts_empty = (
            next_action is not None
            and next_action[ACTION] == "is.workflow.actions.setvariable"
            and "WFInput" not in next_action[PARAMETERS]
        )
        if is_nothing(action) and not starts_empty:
            continue
        new_index[i] = len(kept)
        kept.append(action)
    shortcut["WFWorkflowActions"] = kept
    # The import questions point at their action by position
    for question in shortcut["WFWorkflowImportQuestions"]:
        question["ActionIndex"] = new_index[question["ActionIndex"]]


def main() -> None:
    shortcut = compile_source()
    make_numbers(shortcut)
    remove_nothing(shortcut)
    OUTPUT.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY))
    print(f"Wrote {OUTPUT.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
