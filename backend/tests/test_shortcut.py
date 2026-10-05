"""The Apple Shortcut must keep sending what the API accepts and reading what it returns
(see docs/shortcut.md)."""

import plistlib
from pathlib import Path

from app.api.fuel_ups import FuelUpCreate, FuelUpOut
from app.api.reference import FuelTypesOut, OdometerOut, VehicleOut
from tests.conftest import AppUnderTest
from tests.helpers import sign_in_admin

SHORTCUT = Path(__file__).parents[2] / "shortcut" / "Fuel Tracker.shortcut"
NUMBER_ITEM = 3  # a dictionary item of type "number" (0 is text)
WHOLE_NUMBERS = {"vehicle_id", "odometer"}
DECIMALS = {"latitude", "longitude", "quantity", "total_price"}
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


def json_bodies() -> list[dict[str, dict]]:
    """The items (by field name) of each JSON body the Shortcut sends."""
    bodies = []
    for action in actions():
        params = action.get("WFWorkflowActionParameters", {})
        if params.get("WFHTTPBodyType") == "JSON":
            items = params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
            bodies.append({i["WFKey"]["Value"]["string"]: i for i in items})
    return bodies


def test_shortcut_sends_fields_the_api_knows() -> None:
    bodies = json_bodies()
    assert bodies, "the Shortcut sends no fuel-up"
    for body in bodies:
        assert set(body) <= set(FuelUpCreate.model_fields)


def test_shortcut_sends_whole_numbers_as_json_numbers() -> None:
    for body in json_bodies():
        for field in WHOLE_NUMBERS & set(body):
            assert body[field].get("WFItemType", 0) == NUMBER_ITEM, field


def test_shortcut_sends_decimals_as_text_with_a_decimal_point() -> None:
    # As a number, a phone set to German turned 52,34 into 5234 on the way to the server
    by_uuid = {
        a["WFWorkflowActionParameters"]["UUID"]: a
        for a in actions()
        if "UUID" in a.get("WFWorkflowActionParameters", {})
    }
    sent = set()
    for body in json_bodies():
        for field in DECIMALS & set(body):
            item = body[field]
            assert item.get("WFItemType", 0) == 0, field
            (attachment,) = item["WFValue"]["Value"]["attachmentsByRange"].values()
            source = by_uuid[attachment["OutputUUID"]]
            assert source["WFWorkflowActionIdentifier"] == "is.workflow.actions.text.replace"
            replace = source["WFWorkflowActionParameters"]
            assert (replace["WFReplaceTextFind"], replace["WFReplaceTextReplace"]) == (",", ".")
            sent.add(field)
    assert sent == DECIMALS


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


async def test_api_takes_the_decimals_as_text(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    response = await api.client.post(
        "/api/fuel-ups",
        json={
            "vehicle_id": 1,
            "odometer": 12345,
            "latitude": "52.343502902871",
            "longitude": "8.318185007196",
            "fuel_type": "Diesel",
            "quantity": "45.5",
            "total_price": "79.34",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["latitude"], created["longitude"]) == (52.343502902871, 8.318185007196)
    assert (created["quantity"], created["total_price"]) == ("45.500", "79.34")
