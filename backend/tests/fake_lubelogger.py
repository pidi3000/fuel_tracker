"""A stand-in for LubeLogger's API, matching what the real one answers (checked against its source
and a real instance): camelCase JSON, ISO dates and plain numbers with `culture-invariant`."""

import json
import uuid
from urllib.parse import parse_qs

import httpx


class FakeLubeLogger:
    def __init__(self) -> None:
        self.vehicles: list[dict] = [
            {
                "id": 1,
                "year": 2020,
                "make": "VW",
                "model": "Golf",
                "licensePlate": "TEST-1",
                "vehicleIdentifier": "LicensePlate",
                "extraFields": [],
            },
            {
                "id": 2,
                "year": 2018,
                "make": "Opel",
                "model": "Corsa",
                "licensePlate": "",
                "vehicleIdentifier": "Nickname",
                "extraFields": [{"name": "Nickname", "value": "Red one"}],
            },
        ]
        self.records: list[dict] = []
        self.uploads: dict[str, bytes] = {}
        self.extra_fields = {"GasRecord": ["GPS Location", "Address"]}
        self.api_key: str | None = None
        self.other_odometer: dict[int, int] = {}  # readings from other record types
        # Failure injection
        self.down = False  # connection errors
        self.error_status: int | None = None  # answer every request with this status
        self.fail_add: list[int] = []  # statuses for the next add calls
        self.add_then_lose_response = 0  # create the record, then drop the connection
        self.requests: list[httpx.Request] = []

    # --- helpers ---

    def latest_odometer(self, vehicle_id: int) -> int:
        readings = [r["odometer"] for r in self.records if r["vehicleId"] == vehicle_id]
        readings.append(self.other_odometer.get(vehicle_id, 0))
        return max(readings)

    def add_record(self, vehicle_id: int, **fields) -> dict:
        record = {
            "vehicleId": vehicle_id,
            "id": len(self.records) + 1,
            "fuelEconomy": 0,
            "startingSoc": 0,
            "endingSoc": 0,
            "tags": "",
            "notes": "",
            "extraFields": [],
            "files": [],
            "isFillToFull": True,
            "missedFuelUp": False,
            **fields,
        }
        self.records.append(record)
        return record

    # --- the HTTP side ---

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if self.error_status:
            return httpx.Response(self.error_status, json={"success": False, "message": "boom"})
        if self.api_key is not None and request.headers.get("x-api-key") != self.api_key:
            return httpx.Response(403, json={"success": False, "message": "Access Denied"})
        assert request.headers.get("culture-invariant") == "true"

        path = request.url.path
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        vehicle_id = int(query.get("vehicleId", 0))

        if path == "/api/vehicles":
            return httpx.Response(200, json=self.vehicles)
        if path == "/api/vehicle/odometerrecords/latest":
            return httpx.Response(200, json=self.latest_odometer(vehicle_id))
        if path == "/api/vehicle/gasrecords":
            return httpx.Response(
                200, json=[r for r in self.records if r["vehicleId"] == vehicle_id]
            )
        if path == "/api/vehicle/gasrecords/all":
            return httpx.Response(200, json=self.records)
        if path == "/api/extrafields":
            return httpx.Response(
                200,
                json=[
                    {
                        "recordType": record_type,
                        "extraFields": [
                            {"name": n, "isRequired": "False", "fieldType": "Text"} for n in names
                        ],
                    }
                    for record_type, names in self.extra_fields.items()
                ],
            )
        if path == "/api/documents/upload":
            return self._upload(request)
        if path == "/api/vehicle/gasrecords/add":
            return self._add(request, vehicle_id)
        return httpx.Response(404)

    def _upload(self, request: httpx.Request) -> httpx.Response:
        # The multipart body carries the original file name and the bytes
        body = request.content
        assert b'name="documents"' in body, "the form field must be called 'documents'"
        marker = b'filename="'
        start = body.index(marker) + len(marker)
        filename = body[start : body.index(b'"', start)].decode()
        location = f"/documents/{uuid.uuid4()}.pdf"
        self.uploads[location] = body
        return httpx.Response(
            200, json=[{"name": filename, "location": location, "isPending": False}]
        )

    def _add(self, request: httpx.Request, vehicle_id: int) -> httpx.Response:
        if self.fail_add:
            status = self.fail_add.pop(0)
            return httpx.Response(status, json={"success": False, "message": "Rejected"})
        data = json.loads(request.content)
        required = ("date", "odometer", "fuelConsumed", "cost", "isFillToFull", "missedFuelUp")
        if any(data.get(key) in (None, "") for key in required):
            return httpx.Response(
                400,
                json={"success": False, "message": "Input object invalid, ... cannot be empty."},
            )
        for key in ("odometer", "fuelConsumed", "cost"):
            # numbers must be sent as JSON numbers (see the client's docstring)
            assert isinstance(data[key], int | float), f"{key} must be a JSON number"
        record = self.add_record(
            vehicle_id,
            date=data["date"],
            odometer=data["odometer"],
            fuelConsumed=data["fuelConsumed"],
            cost=data["cost"],
            isFillToFull=data["isFillToFull"],
            missedFuelUp=data["missedFuelUp"],
            notes=data.get("notes", ""),
            extraFields=data.get("extraFields", []),
            files=[{**f, "isPending": False} for f in data.get("files", [])],
        )
        if self.add_then_lose_response:
            self.add_then_lose_response -= 1
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "message": "Gas Record Added",
                "additionalData": {"recordId": record["id"]},
            },
        )
