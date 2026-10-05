"""The Apple Shortcut must keep sending what the API accepts (see docs/shortcut.md)."""

import plistlib
from pathlib import Path

from app.api.fuel_ups import FuelUpCreate

SHORTCUT = Path(__file__).parents[2] / "shortcut" / "Fuel Tracker.shortcut"
NUMBER_ITEM = 3  # a dictionary item of type "number" (0 is text)
NUMBERS = {"vehicle_id", "odometer", "latitude", "longitude", "quantity", "total_price"}


def request_bodies() -> list[dict[str, int]]:
    """The field names of each JSON body the Shortcut sends, with the type of each value."""
    shortcut = plistlib.loads(SHORTCUT.read_bytes())
    bodies = []
    for action in shortcut["WFWorkflowActions"]:
        params = action.get("WFWorkflowActionParameters", {})
        if params.get("WFHTTPBodyType") == "JSON":
            items = params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
            bodies.append({i["WFKey"]["Value"]["string"]: i.get("WFItemType", 0) for i in items})
    return bodies


def test_shortcut_sends_fields_the_api_knows() -> None:
    bodies = request_bodies()
    assert bodies, "the Shortcut sends no fuel-up"
    for body in bodies:
        assert set(body) <= set(FuelUpCreate.model_fields)


def test_shortcut_sends_numbers_as_json_numbers() -> None:
    # A text value would carry the decimal comma of a European phone ("45,5") to the server
    for body in request_bodies():
        for field in NUMBERS & set(body):
            assert body[field] == NUMBER_ITEM, field
