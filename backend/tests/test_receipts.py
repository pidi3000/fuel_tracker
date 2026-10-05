import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.types import utcnow
from app.models import Receipt, ReceiptState
from app.services import receipts as receipt_service
from app.services.mail import MailWatcher, ReceiptMailHandler
from app.services.receipts import pending_email_moves
from tests.conftest import AppUnderTest
from tests.fake_mailbox import FakeMailbox, build_email, receipt_pdf
from tests.helpers import create_user, get_fuel_up, sign_in_admin, sign_in_as

RECEIPT_TIME = datetime(2026, 9, 23, 15, 8, tzinfo=UTC)  # the fixture receipt, 17:08 in Berlin


def make_watcher(api: AppUnderTest, sender: str | None = None) -> MailWatcher:
    handler = ReceiptMailHandler(
        api.services.sessionmaker, api.services.receipts, api.services.processor.wake
    )
    settings = api.services.settings
    return MailWatcher(
        FakeMailbox,
        handler,
        asyncio.get_running_loop(),
        sender=sender if sender is not None else settings.receipt_sender,
        subject_pattern=settings.receipt_subject_pattern,
        processed_folder="Processed",
        poll_seconds=1,
        reconnect_delays=(0.05,),
    )


async def deliver(api: AppUnderTest, mailbox: FakeMailbox, raw: bytes, **kwargs) -> int:
    """Put an email in the mailbox and let the watcher process the inbox."""
    mailbox.deliver(raw, **kwargs)
    return await asyncio.to_thread(make_watcher(api).process_inbox, mailbox)


async def receipt_fuel_up(api: AppUnderTest, **overrides) -> dict:
    body = {"payment_source": "email_receipt", "fuel_up_time": "2026-09-23T15:05:00Z"}
    body.update(overrides)
    for key in ("fuel_type", "quantity", "total_price"):
        body.setdefault(key, None)
    response = await api.client.post(
        "/api/fuel-ups", json={"vehicle_id": 1, "odometer": 12345, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def all_receipts(api: AppUnderTest) -> list[Receipt]:
    async with api.services.sessionmaker() as session:
        return list((await session.execute(select(Receipt).order_by(Receipt.id))).scalars())


async def insert_receipt(api: AppUnderTest, **fields) -> int:
    values = {
        "message_id": f"<test-{utcnow().timestamp()}-{len(fields)}@x>",
        "paid_at": RECEIPT_TIME,
        "station": "TESTOIL",
        "address": "TESTOIL, Musterstrasse 12, 12345 Musterstadt",
        "printed_date": "9/23/2026, 5:08 PM",
        "fuel_type": "Super",
        "quantity": Decimal("23.00"),
        "unit": "L",
        "total": Decimal("41.15"),
        "currency": "EUR",
        "transaction_id": f"tx-{len(fields)}-{id(fields)}",
        "missing": [],
        "warnings": [],
    }
    values.update(fields)
    async with api.services.sessionmaker() as session:
        receipt = Receipt(**values)
        session.add(receipt)
        await session.commit()
        return receipt.id


async def test_receipt_arrives_after_the_fuel_up(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    assert created["status"] == "pending" and created["waiting_for_receipt"] is True
    assert created["receipt_deadline"] is not None and created["fuel_type"] is None

    mailbox = FakeMailbox()
    assert await deliver(api, mailbox, build_email(pdf=receipt_pdf())) == 1
    assert mailbox.moved == [(1, "Processed")]
    assert mailbox.marked_read == [1]

    matched = await get_fuel_up(api, created["id"])
    assert matched["status"] == "pending" and matched["sending"] is True
    assert matched["fuel_type"] == "Super" and Decimal(matched["quantity"]) == 23
    assert matched["total_price"] == "41.15" and matched["receipt_id"] is not None
    assert matched["address"] == "TESTOIL, Musterstraße 12, 12345 Musterstadt"

    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    (record,) = api.fake.records
    assert record["fuelConsumed"] == 23.0 and record["cost"] == 41.15
    assert record["notes"] == (
        "Fuel type: Super\nPayment: Pace Drive email receipt\nCreated by: alice\n"
        "PaceDrive Transaction ID: 00000000-1111-4222-8333-444444444444"
    )
    assert {f["name"]: f["value"] for f in record["extraFields"]}["Address"] == (
        "TESTOIL, Musterstraße 12, 12345 Musterstadt"
    )
    # The receipt PDF is attached to the record
    (file,) = record["files"]
    assert file["name"] == "receipt.pdf" and file["location"] in api.fake.uploads
    assert receipt_pdf() in api.fake.uploads[file["location"]]


async def test_receipt_arrives_before_the_fuel_up(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    (receipt,) = await all_receipts(api)
    assert receipt.state == ReceiptState.UNMATCHED and receipt.paid_at == RECEIPT_TIME

    created = await receipt_fuel_up(api)  # matched while it is created
    assert created["receipt_id"] == receipt.id and created["fuel_type"] == "Super"
    assert created["waiting_for_receipt"] is False


async def test_the_receipt_must_be_within_the_matching_window(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    # 15:08 vs 15:30: 22 minutes apart, the window is 10
    created = await receipt_fuel_up(api, fuel_up_time="2026-09-23T15:30:00Z")
    assert created["receipt_id"] is None and created["waiting_for_receipt"] is True
    # Wider window and the periodic matching picks it up
    await api.services.runtime.set("match_window_minutes", 30)
    async with api.services.sessionmaker() as session:
        assert await receipt_service.rematch_waiting(session, api.services.receipts) == 1
        await session.commit()
    assert (await get_fuel_up(api, created["id"]))["receipt_id"] is not None


async def test_editing_the_time_matches_a_waiting_receipt(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    created = await receipt_fuel_up(api, fuel_up_time="2026-09-23T10:00:00Z")
    assert created["receipt_id"] is None
    edited = await api.client.patch(
        f"/api/fuel-ups/{created['id']}", json={"fuel_up_time": "2026-09-23T15:09:00Z"}
    )
    assert edited.json()["receipt_id"] is not None


async def test_the_closest_fuel_up_gets_the_receipt(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    far = await receipt_fuel_up(api, fuel_up_time="2026-09-23T15:15:00Z", odometer=12000)
    near = await receipt_fuel_up(api, fuel_up_time="2026-09-23T15:09:00Z", odometer=12100)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    assert (await get_fuel_up(api, near["id"]))["receipt_id"] is not None
    assert (await get_fuel_up(api, far["id"]))["receipt_id"] is None


async def test_review_before_send_after_the_receipt(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    created = await receipt_fuel_up(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))

    state = await get_fuel_up(api, created["id"])
    assert state["status"] == "needs_attention" and state["attention"] == "review"
    note = (await api.client.get("/api/notifications")).json()[0]
    assert "arrived" in note["title"] and note["level"] == "info"

    await api.client.post(f"/api/fuel-ups/{created['id']}/approve")
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"


async def test_unit_mismatch_needs_attention(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    await insert_receipt(api, quantity=Decimal("6.08"), unit="gal")
    async with api.services.sessionmaker() as session:
        assert await receipt_service.rematch_waiting(session, api.services.receipts) == 1
        await session.commit()

    state = await get_fuel_up(api, created["id"])
    assert state["status"] == "needs_attention" and state["attention"] == "unit_mismatch"
    assert "gal" in state["attention_message"] and "L" in state["attention_message"]
    assert (await api.client.get("/api/notifications")).json()[0]["level"] == "warning"
    assert await api.services.processor.process_due() == 0

    # Convert by hand, then approve
    await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"quantity": "23.0"})
    approved = await api.client.post(f"/api/fuel-ups/{created['id']}/approve")
    assert approved.status_code == 200
    await api.services.processor.process_due()
    assert api.fake.records[0]["fuelConsumed"] == 23.0


async def test_currency_mismatch_needs_attention(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    await insert_receipt(api, currency="USD")
    async with api.services.sessionmaker() as session:
        await receipt_service.rematch_waiting(session, api.services.receipts)
        await session.commit()
    state = await get_fuel_up(api, created["id"])
    assert state["status"] == "needs_attention" and "USD" in state["attention_message"]


async def test_unreadable_values_need_attention(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    await insert_receipt(api, total=None, missing=["total"])
    async with api.services.sessionmaker() as session:
        await receipt_service.rematch_waiting(session, api.services.receipts)
        await session.commit()
    state = await get_fuel_up(api, created["id"])
    assert state["status"] == "needs_attention" and state["attention"] == "unreadable"
    assert "total price" in state["attention_message"]
    # The missing value can be entered by hand
    await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"total_price": "41.15"})
    assert (await api.client.post(f"/api/fuel-ups/{created['id']}/approve")).status_code == 200


async def test_date_taken_from_the_pdf_is_reported(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    await insert_receipt(api, warnings=["date_from_pdf_metadata"])
    async with api.services.sessionmaker() as session:
        await receipt_service.rematch_waiting(session, api.services.receipts)
        await session.commit()
    state = await get_fuel_up(api, created["id"])
    assert state["warnings"] == ["receipt: The date was taken from the PDF file."]
    assert state["status"] == "pending" and state["sending"] is True  # still processed
    note = (await api.client.get("/api/notifications")).json()[0]
    assert note["level"] == "warning" and "PDF" in note["title"]


async def test_waiting_too_long_fails_and_can_be_retried(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    async with api.services.sessionmaker() as session:
        later = utcnow() + timedelta(minutes=61)
        assert (
            await receipt_service.expire_waiting_fuel_ups(session, api.services.receipts, later)
            == 1
        )
        await session.commit()

    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed" and "60 minutes" in failed["error_message"]
    note = (await api.client.get("/api/notifications")).json()[0]
    assert note["level"] == "error" and note["fuel_up_id"] == created["id"]

    # The receipt shows up late; retrying finds it
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    assert (await get_fuel_up(api, created["id"]))["status"] == "failed"  # not waiting any more
    retried = (await api.client.post(f"/api/fuel-ups/{created['id']}/retry")).json()
    assert retried["receipt_id"] is not None and retried["sending"] is True


async def test_not_yet_expired_fuel_ups_keep_waiting(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    async with api.services.sessionmaker() as session:
        later = utcnow() + timedelta(minutes=59)
        assert (
            await receipt_service.expire_waiting_fuel_ups(session, api.services.receipts, later)
            == 0
        )
    assert (await get_fuel_up(api, created["id"]))["status"] == "pending"


async def test_enter_the_payment_by_hand_after_a_failure(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    async with api.services.sessionmaker() as session:
        await receipt_service.expire_waiting_fuel_ups(
            session, api.services.receipts, utcnow() + timedelta(hours=2)
        )
        await session.commit()
    edited = await api.client.patch(
        f"/api/fuel-ups/{created['id']}",
        json={
            "payment_source": "manual",
            "fuel_type": "Super",
            "quantity": "20",
            "total_price": "40",
        },
    )
    assert edited.status_code == 200 and edited.json()["payment_source"] == "manual"
    await api.client.post(f"/api/fuel-ups/{created['id']}/retry")
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    assert api.fake.records[0]["notes"].startswith("Fuel type: Super\nPayment: Manual")


async def test_payment_data_of_a_waiting_fuel_up_cannot_be_edited(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    response = await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"fuel_type": "Super"})
    assert response.status_code == 409


async def test_payment_data_sent_with_a_receipt_fuel_up_is_ignored(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api, fuel_type="Diesel", quantity="1", total_price="1")
    assert created["fuel_type"] is None and created["quantity"] is None


async def notification_titles(api: AppUnderTest) -> list[str]:
    return [n["title"] for n in (await api.client.get("/api/notifications")).json()]


async def test_an_email_stays_in_the_inbox_until_its_receipt_is_linked(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    raw = build_email(pdf=receipt_pdf(), message_id="<a@x>")
    assert await deliver(api, mailbox, raw, message_id="<a@x>") == 0
    assert len(await all_receipts(api)) == 1
    assert len(mailbox.messages) == 1 and mailbox.moved == []

    # Looked at again (and again): nothing happens, it isn't taken for a second copy
    watcher = make_watcher(api)
    for _ in range(2):
        assert await asyncio.to_thread(watcher.process_inbox, mailbox) == 0
    assert len(mailbox.messages) == 1 and mailbox.flagged == []
    assert mailbox.fetched == [1], "an email that is waiting isn't read again"
    assert "A receipt email was found again" not in await notification_titles(api)

    # The fuel-up arrives later and takes the receipt; now the email can go
    created = await receipt_fuel_up(api)
    assert (await get_fuel_up(api, created["id"]))["receipt_id"] is not None
    assert await asyncio.to_thread(watcher.process_inbox, mailbox) == 1
    assert mailbox.moved == [(1, "Processed")] and mailbox.marked_read == [1]
    assert mailbox.flagged == [] and mailbox.messages == {}
    assert "A receipt email was found again" not in await notification_titles(api)
    assert [r.email_moved for r in await all_receipts(api)] == [True]


async def test_an_ignored_receipt_email_is_moved(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>"))
    (receipt,) = await all_receipts(api)
    assert (await api.client.post(f"/api/receipts/{receipt.id}/ignore")).status_code == 204
    async with api.services.sessionmaker() as session:
        assert await pending_email_moves(session) == {receipt.id}
    assert await asyncio.to_thread(make_watcher(api).process_inbox, mailbox) == 1
    assert mailbox.moved == [(1, "Processed")] and mailbox.flagged == []
    async with api.services.sessionmaker() as session:
        assert await pending_email_moves(session) == set()


async def test_a_second_copy_of_a_moved_email_is_flagged(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    await receipt_fuel_up(api)
    assert await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>")) == 1
    assert mailbox.flagged == []
    # The same email arrives again, and again as a new email with the same receipt
    assert await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>")) == 1
    assert await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<b@x>")) == 1
    assert len(await all_receipts(api)) == 1
    assert [folder for _, folder in mailbox.moved] == ["Processed"] * 3
    assert mailbox.flagged == [2, 3] and mailbox.messages == {}
    titles = await notification_titles(api)
    assert titles.count("A receipt email was found again") == 2


async def test_two_copies_in_the_inbox_leave_together_once_linked(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>"))
    await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>"))
    assert len(mailbox.messages) == 2 and mailbox.moved == []
    await receipt_fuel_up(api)
    assert await asyncio.to_thread(make_watcher(api).process_inbox, mailbox) == 2
    assert mailbox.messages == {} and mailbox.flagged == [
        2
    ]  # the first is the email, the second its copy


async def test_a_new_email_with_a_stored_transaction_is_flagged_at_once(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<a@x>"))
    # Same receipt in another email while the first still waits in the inbox
    assert await deliver(api, mailbox, build_email(pdf=receipt_pdf(), message_id="<b@x>")) == 1
    assert mailbox.flagged == [2] and list(mailbox.messages) == [1]
    assert "A receipt email was found again" in await notification_titles(api)


async def test_receipts_can_come_from_several_senders(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    pdf = receipt_pdf()
    old = "old.account@example.org"
    mailbox.deliver(build_email(pdf=pdf, sender=old, message_id="<old@x>"), sender=old)
    mailbox.deliver(build_email(pdf=pdf, message_id="<new@x>"))
    stranger = "someone@example.com"
    mailbox.deliver(build_email(pdf=pdf, sender=stranger), sender=stranger)

    # Only the configured sender: the old account's email is not a receipt
    only_pace = make_watcher(api)
    assert [only_pace.is_receipt(h) for h in mailbox.headers()] == [False, True, False]

    # Several senders, separated by commas (spaces and capitals don't matter)
    several = make_watcher(api, sender="no-reply@connectedfueling.com, Old.Account@example.org")
    assert [several.is_receipt(h) for h in mailbox.headers()] == [True, True, False]
    # Both emails carry the same receipt: the first is stored and waits for a fuel-up, the
    # second is a repeat. The stranger's email is left alone.
    assert await asyncio.to_thread(several.process_inbox, mailbox) == 1
    assert len(await all_receipts(api)) == 1
    assert list(mailbox.messages) == [1, 3] and mailbox.flagged == [2]
    # The subject still has to match
    mailbox.deliver(build_email(pdf=pdf, sender=old, subject="Hello"), sender=old, subject="Hello")
    assert several.is_receipt(mailbox.headers()[-1]) is False


async def test_emails_that_are_not_receipts_are_left_alone(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    pdf = receipt_pdf()
    mailbox.deliver(
        build_email(pdf=pdf, sender="someone@example.com"), sender="someone@example.com"
    )
    mailbox.deliver(build_email(pdf=pdf, subject="Hello"), subject="Hello")
    mailbox.deliver(build_email(pdf=None))  # a receipt mail without a PDF
    moved = await asyncio.to_thread(make_watcher(api).process_inbox, mailbox)
    assert moved == 0 and len(mailbox.messages) == 3 and await all_receipts(api) == []


async def test_an_unreadable_pdf_is_kept_and_reported(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    # It stays in the inbox until the receipt is ignored
    assert await deliver(api, mailbox, build_email(pdf=b"%PDF-1.4 broken")) == 0
    (receipt,) = await all_receipts(api)
    assert receipt.parse_error and receipt.paid_at is None
    note = (await api.client.get("/api/notifications")).json()[0]
    assert note["level"] == "error" and "couldn't be read" in note["title"]
    # It can't become a fuel-up, but can be ignored
    assert (
        await api.client.post(
            f"/api/receipts/{receipt.id}/complete", json={"vehicle_id": 1, "odometer": 1}
        )
    ).status_code == 422
    assert (await api.client.post(f"/api/receipts/{receipt.id}/ignore")).status_code == 204


async def test_a_receipt_without_a_fuel_up_is_listed_and_can_be_completed(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    (listed,) = (await api.client.get("/api/receipts")).json()
    assert listed["state"] == "unmatched" and listed["station"] == "TESTOIL"
    assert listed["fuel_up_id"] is None and listed["has_pdf"] is True
    assert listed["total"] == "41.15" and listed["quantity"] == "23.00"

    done = await api.client.post(
        f"/api/receipts/{listed['id']}/complete",
        json={"vehicle_id": 1, "odometer": 12345, "latitude": 52.2, "longitude": 8.8},
    )
    assert done.status_code == 201, done.text
    fuel_up = done.json()
    assert fuel_up["receipt_id"] == listed["id"] and fuel_up["fuel_type"] == "Super"
    assert fuel_up["fuel_up_time"].startswith("2026-09-23T15:08:00")
    assert (await api.client.get("/api/receipts")).json() == []
    assert (await api.client.get(f"/api/receipts/{listed['id']}")).json()["fuel_up_id"] == fuel_up[
        "id"
    ]

    again = await api.client.post(
        f"/api/receipts/{listed['id']}/complete", json={"vehicle_id": 1, "odometer": 12346}
    )
    assert again.status_code == 409
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, fuel_up["id"]))["status"] == "done"


async def test_an_unneeded_receipt_can_be_ignored(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    (listed,) = (await api.client.get("/api/receipts")).json()
    assert (await api.client.post(f"/api/receipts/{listed['id']}/ignore")).status_code == 204
    assert (await api.client.get("/api/receipts")).json() == []
    ignored = (await api.client.get("/api/receipts?state=ignored")).json()
    assert [r["id"] for r in ignored] == [listed["id"]]
    assert (await api.client.post(f"/api/receipts/{listed['id']}/ignore")).status_code == 409
    # An ignored receipt never matches a fuel-up
    created = await receipt_fuel_up(api)
    assert created["receipt_id"] is None


async def test_the_receipt_pdf_can_be_downloaded(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    (listed,) = (await api.client.get("/api/receipts")).json()
    response = await api.client.get(f"/api/receipts/{listed['id']}/pdf")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content == receipt_pdf()
    assert (await api.client.get("/api/receipts/999/pdf")).status_code == 404


async def test_a_receipt_already_in_lubelogger_is_not_used_again(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.fake.add_record(
        2,
        date="2026-09-23",
        odometer=500,
        fuelConsumed=23,
        cost=41.15,
        notes="PaceDrive Transaction ID: 00000000-1111-4222-8333-444444444444",
    )
    created = await receipt_fuel_up(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    await api.services.processor.process_due()
    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed"
    assert "already in LubeLogger" in failed["error_message"] and "#1" in failed["error_message"]
    assert len(api.fake.records) == 1


async def test_the_pdf_is_uploaded_only_once(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.services.processor._retry_delays = (0,)
    created = await receipt_fuel_up(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    api.fake.fail_add = [503]  # the first try uploads, then LubeLogger errors
    await api.services.processor.process_due()
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    assert len(api.fake.uploads) == 1


async def test_lonely_receipts_are_announced_after_the_window(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    async with api.services.sessionmaker() as session:
        assert await receipt_service.notify_lonely_receipts(session, api.services.receipts) == 0
        later = utcnow() + timedelta(minutes=11)
        assert (
            await receipt_service.notify_lonely_receipts(session, api.services.receipts, later) == 1
        )
        assert (
            await receipt_service.notify_lonely_receipts(session, api.services.receipts, later) == 0
        )
        await session.commit()
    (note,) = (await api.client.get("/api/notifications")).json()
    assert note["title"] == "Receipt without a fuel-up" and "TESTOIL" in note["message"]


async def test_receipts_are_visible_to_users_with_access_only_once_matched(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    await create_user(api, "bob", [1])
    await create_user(api, "carol", [2])
    await receipt_fuel_up(api)  # vehicle 1
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    (matched,) = [r for r in await all_receipts(api)]

    await sign_in_as(api, "bob")
    assert (await api.client.get(f"/api/receipts/{matched.id}")).status_code == 200
    await sign_in_as(api, "carol")
    assert (await api.client.get(f"/api/receipts/{matched.id}")).status_code == 404
    assert (await api.client.get(f"/api/receipts/{matched.id}/pdf")).status_code == 404


async def test_status_reports_the_mailbox(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    status = (await api.client.get("/api/status")).json()
    assert status["mailbox"]["state"] == "not_configured"


# --- the watcher thread ---


async def wait_for(predicate, seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


async def test_the_watcher_thread_processes_new_mail_and_stops(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await receipt_fuel_up(api)  # takes the receipt, so its email can be moved
    mailbox = FakeMailbox()
    watcher = make_watcher(api)
    watcher._factory = lambda: mailbox
    watcher.start()
    try:
        await wait_for(lambda: watcher.status.state == "ok")
        mailbox.deliver(build_email(pdf=receipt_pdf()))  # wakes the waiting watcher
        await wait_for(lambda: len(mailbox.moved) == 1)
        assert len(await all_receipts(api)) == 1
    finally:
        await asyncio.to_thread(watcher.stop)
    assert mailbox.closed >= 1 and watcher.status.state == "disabled"


async def test_the_watcher_reconnects_after_a_failure(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    mailbox = FakeMailbox()
    mailbox.connect_error = ConnectionError("server down")
    watcher = make_watcher(api)
    watcher._factory = lambda: mailbox
    watcher.start()
    try:
        await wait_for(lambda: watcher.status.state == "error")
        assert "server down" in watcher.status.message
        mailbox.connect_error = None
        await wait_for(lambda: watcher.status.state == "ok")
        assert mailbox.connects >= 2
    finally:
        await asyncio.to_thread(watcher.stop)


async def test_check_now_looks_at_the_inbox_without_waiting(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await receipt_fuel_up(api)  # takes the receipt, so its email can be moved
    mailbox = FakeMailbox()
    watcher = make_watcher(api)
    watcher._factory = lambda: mailbox
    watcher._poll_seconds = 60
    watcher.start()
    try:
        await wait_for(lambda: watcher.status.state == "ok")
        # New mail that the server never announced
        mailbox.messages[99] = (
            __import__("app.services.mail", fromlist=["MailHeader"]).MailHeader(
                99, "PACE <no-reply@connectedfueling.com>", "Receipt | PACE Pay", None
            ),
            build_email(pdf=receipt_pdf()),
        )
        watcher.check_now()
        await wait_for(lambda: len(mailbox.moved) == 1)
    finally:
        await asyncio.to_thread(watcher.stop)


def test_which_emails_are_receipts(api=None) -> None:
    from app.services.mail import MailHeader

    watcher = MailWatcher(
        FakeMailbox,
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        sender="no-reply@connectedfueling.com",
        subject_pattern=r"\|\s*PACE Pay\s*$",
        processed_folder="Processed",
    )
    ok = MailHeader(1, "PACE <No-Reply@ConnectedFueling.com>", "Your receipt | PACE Pay", None)
    assert watcher.is_receipt(ok)
    assert watcher.is_receipt(MailHeader(1, "no-reply@connectedfueling.com", "x | pace pay ", None))
    assert not watcher.is_receipt(MailHeader(1, "evil@example.com", "x | PACE Pay", None))
    assert not watcher.is_receipt(
        MailHeader(1, "no-reply@connectedfueling.com", "Newsletter", None)
    )
    assert not watcher.is_receipt(
        MailHeader(1, "no-reply@connectedfueling.com.evil.com", "x", None)
    )
