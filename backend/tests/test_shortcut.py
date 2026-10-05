"""The Apple Shortcut must keep sending what the API accepts and reading what it returns
(see docs/shortcut.md)."""

import plistlib
from pathlib import Path

from app.api.fuel_ups import FuelUpCreate, FuelUpOut
from app.api.reference import FuelTypesOut, OdometerOut, VehicleOut

SHORTCUT = Path(__file__).parents[2] / "shortcut" / "Fuel Tracker.shortcut"
NUMBER_ITEM = 3  # a dictionary item of type "number" (0 is text)
NUMBERS = {"vehicle_id", "odometer", "latitude", "longitude", "quantity", "total_price"}
# What the Shortcut reads: the fields of the answers, and the "detail" of an error
ANSWER_FIELDS = (
    set(FuelUpOut.model_fields)
    | set(VehicleOut.model_fields)
    | set(OdometerOut.model_fields)
    | set(FuelTypesOut.model_fields)
    | {"detail"}
)


def actions() -> list[dict]:
    return plistlib.loads(SHORTCUT.read_bytes())["WFWorkflowActions"]


def request_bodies() -> list[dict[str, int]]:
    """The field names of each JSON body the Shortcut sends, with the type of each value."""
    bodies = []
    for action in actions():
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


def test_shortcut_reads_fields_the_api_returns() -> None:
    keys = {
        a["WFWorkflowActionParameters"]["WFDictionaryKey"]
        for a in actions()
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.getvalueforkey"
    }
    assert keys, "the Shortcut reads nothing from the server"
    assert keys <= ANSWER_FIELDS


def test_shortcut_reads_the_answers_with_get_dictionary_value() -> None:
    # A "property" of a variable is something else (a file's name or size): it is empty for the
    # JSON the server sends, which left the vehicle list and the odometer hint blank
    assert "WFPropertyVariableAggrandizement" not in str(actions())
