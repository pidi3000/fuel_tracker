"""Generate fake Pace Drive receipts for the parser tests.

The receipts copy the layout and wording of a real Pace Drive receipt, but all
station, date, amount and ID values are made up. Each receipt is written as
<name>.pdf next to <name>.json, which holds the values the parser must extract.

Like the real receipts, the PDFs are rendered by headless Chromium, so text
extraction behaves the same way. The PDF creation date (metadata) is set to the
payment time, as on real receipts.

Usage (from this directory):

    pip install pypdf
    python generate.py [--chromium /path/to/chromium]
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter

HERE = Path(__file__).parent
TIME_ZONE = ZoneInfo("Europe/Berlin")
VAT_RATE = Decimal("0.19")
CENT = Decimal("0.01")


@dataclass
class Receipt:
    name: str
    station: str
    street: str
    city: str
    paid_at: datetime  # local time (Europe/Berlin)
    fuel_type: str
    pump: int
    quantity: Decimal  # liters
    price_per_liter: Decimal  # EUR
    transaction_id: str
    token_id: str
    terminal_id: str
    wallet: str

    @property
    def total(self) -> Decimal:
        return (self.quantity * self.price_per_liter).quantize(CENT, ROUND_HALF_UP)

    @property
    def net(self) -> Decimal:
        return (self.total / (1 + VAT_RATE)).quantize(CENT, ROUND_HALF_UP)

    @property
    def vat(self) -> Decimal:
        return self.total - self.net

    @property
    def printed_date(self) -> str:
        # English app format, e.g. "9/23/2026, 5:08 PM"
        hour = self.paid_at.hour % 12 or 12
        suffix = "AM" if self.paid_at.hour < 12 else "PM"
        return f"{self.paid_at.month}/{self.paid_at.day}/{self.paid_at.year}, {hour}:{self.paid_at.minute:02d} {suffix}"

    def expected(self) -> dict:
        return {
            "station": self.station,
            "address": f"{self.station}, {self.street}, {self.city}",
            "printed_date": self.printed_date,
            "paid_at": self.paid_at.isoformat(timespec="minutes"),
            "fuel_type": self.fuel_type,
            "quantity": str(self.quantity),
            "unit": "L",
            "total": str(self.total),
            "currency": "EUR",
            "transaction_id": self.transaction_id,
        }


RECEIPTS = [
    # Afternoon, matches the layout of the original example
    Receipt(
        name="pace_super",
        station="TESTOIL",
        street="Musterstraße 12",
        city="12345 Musterstadt",
        paid_at=datetime(2026, 9, 23, 17, 8, tzinfo=TIME_ZONE),
        fuel_type="Super",
        pump=6,
        quantity=Decimal("23.00"),
        price_per_liter=Decimal("1.789"),
        transaction_id="00000000-1111-4222-8333-444444444444",
        token_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        terminal_id="TST00001",
        wallet="Apple Pay",
    ),
    # Morning, single-digit month/day/hour, winter time (UTC+1)
    Receipt(
        name="pace_diesel_morning",
        station="Beispiel Tankstelle",
        street="Beispielweg 3a",
        city="54321 Beispielhausen",
        paid_at=datetime(2027, 1, 5, 7, 45, tzinfo=TIME_ZONE),
        fuel_type="Diesel",
        pump=2,
        quantity=Decimal("48.37"),
        price_per_liter=Decimal("1.659"),
        transaction_id="12345678-90ab-4cde-8f01-23456789abcd",
        token_id="fedcba98-7654-4321-8fed-cba987654321",
        terminal_id="TST00002",
        wallet="Apple Pay",
    ),
    # Just before midnight on New Year's Eve, multi-word fuel type
    Receipt(
        name="pace_super_e10_midnight",
        station="TESTOIL",
        street="Am Testpark 100",
        city="98765 Prüfdorf",
        paid_at=datetime(2026, 12, 31, 23, 56, tzinfo=TIME_ZONE),
        fuel_type="Super E10",
        pump=11,
        quantity=Decimal("9.81"),
        price_per_liter=Decimal("1.729"),
        transaction_id="0f0f0f0f-a1a1-4b2b-9c3c-d4d4d4d4d4d4",
        token_id="11111111-2222-4333-8444-555555555555",
        terminal_id="TST00003",
        wallet="Apple Pay",
    ),
]

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>about:blank</title>
<style>
  @page {{ size: 252pt 826pt; margin: 0; }}
  body {{ margin: 0; padding: 18pt 14pt; font: 9.5pt "DejaVu Sans", sans-serif;
         color: #222; text-align: center; }}
  .mono {{ font-family: "DejaVu Sans Mono", monospace; }}
  .logo {{ font-weight: bold; color: #1aa3d9; margin-bottom: 14pt; }}
  h1 {{ font-size: 18pt; margin: 4pt 0; }}
  .box {{ background: #eef0f2; border-radius: 6pt; padding: 8pt; margin: 12pt 0;
          text-align: left; }}
  .row {{ display: flex; justify-content: space-between; }}
  .sep {{ border-top: 1px solid #9aa; margin: 6pt 0; }}
  .total {{ font-weight: bold; font-size: 11pt; font-family: "DejaVu Sans", sans-serif; }}
  .ids {{ font-size: 6.5pt; text-align: center; margin-top: 4pt; }}
  .small {{ font-size: 6.5pt; }}
  .big {{ font-size: 16pt; margin: 14pt 0; }}
</style></head><body>
<div class="logo">CONNECTED<br>FUELING</div>
<div>Your fuel stop at</div>
<h1>{station}</h1>
<div>{street}<br>{city}</div>
<div class="box mono">
  <div class="row"><span>Date</span><span>{date}</span></div>
  <div class="sep"></div>
  <div class="row"><span>{fuel_type}</span><span>{total} EUR</span></div>
  <div>Fuel pump {pump}</div>
  <div class="row"><span>· Quantity</span><span>{quantity} L</span></div>
  <div class="row"><span>· Price / L</span><span>{price} EUR</span></div>
  <div class="sep"></div>
  <div class="row total"><span>Total</span><span>{total} EUR</span></div>
  <div class="row"><span>Net</span><span>{net} EUR</span></div>
  <div class="row"><span>VAT 19% A</span><span>{vat} EUR</span></div>
  <div class="ids">Transaction ID: {transaction_id}<br>Token ID: {token_id}<br>Terminal ID: {terminal_id}</div>
</div>
<div><b>Payment made<br>Payment method PACE Pay</b></div>
<div>{wallet}</div>
<p class="small">Your account or card will be charged the above amount. The above
date corresponds to the invoice and service date. Our terms of use
apply: https://legal.pace.cloud/terms/fueling</p>
<div class="big">PACE wishes you a<br>good trip!</div>
<div>PACE Mobility GmbH<br>Haid-und-Neu-Str. 18<br>76131 Karlsruhe<br>HRB 722293<br>
Amtsgericht Mannheim<br>VAT ID: DE300495190</div>
<p>Email: support@connectedfueling.com<br>Phone: +49 721 276664-44</p>
<p class="small">The sales price for fuel includes the statutory stockpiling
contribution.</p>
<p class="small">Information on the efficient use of fuels and on providers of
measures for energy efficiency improvements and energy
savings can be found at www.bfee-online.de and at
www.energiespartipps-oel.de/auto.</p>
</body></html>
"""


def render_html(receipt: Receipt) -> str:
    values = {
        "station": receipt.station,
        "street": receipt.street,
        "city": receipt.city,
        "date": receipt.printed_date,
        "fuel_type": receipt.fuel_type,
        "pump": receipt.pump,
        "quantity": receipt.quantity,
        "price": receipt.price_per_liter,
        "total": receipt.total,
        "net": receipt.net,
        "vat": receipt.vat,
        "transaction_id": receipt.transaction_id,
        "token_id": receipt.token_id,
        "terminal_id": receipt.terminal_id,
        "wallet": receipt.wallet,
    }
    return TEMPLATE.format(**{k: html.escape(str(v)) for k, v in values.items()})


def pdf_date(moment: datetime) -> str:
    utc = moment.astimezone(ZoneInfo("UTC"))
    return utc.strftime("D:%Y%m%d%H%M%S+00'00'")


def generate(receipt: Receipt, chromium: str) -> None:
    pdf_path = HERE / f"{receipt.name}.pdf"
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "receipt.html"
        raw_pdf = Path(tmp) / "raw.pdf"
        html_path.write_text(render_html(receipt), encoding="utf-8")
        subprocess.run(
            [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={raw_pdf}",
                html_path.as_uri(),
            ],
            check=True,
            capture_output=True,
        )

        # Real receipts are generated a few seconds after the payment
        created = pdf_date(receipt.paid_at + timedelta(seconds=40))
        writer = PdfWriter(clone_from=PdfReader(raw_pdf))
        writer.metadata = {
            "/Title": "about:blank",
            "/Creator": "HeadlessChrome",
            "/Producer": "Skia/PDF",
            "/CreationDate": created,
            "/ModDate": created,
        }
        with pdf_path.open("wb") as f:
            writer.write(f)

    json_path = HERE / f"{receipt.name}.json"
    json_path.write_text(
        json.dumps(receipt.expected(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {pdf_path.name}, {json_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--chromium",
        default=shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome"),
        help="path to a Chromium or Chrome binary",
    )
    args = parser.parse_args()
    if not args.chromium:
        parser.error("Chromium not found, pass --chromium")
    for receipt in RECEIPTS:
        generate(receipt, args.chromium)


if __name__ == "__main__":
    main()
