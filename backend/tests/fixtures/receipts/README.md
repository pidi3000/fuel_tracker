# Example Pace Drive receipts

Fake receipts for the receipt parser tests. They copy the layout and wording of
a real Pace Drive receipt (English app), but every station, address, date,
amount and ID is made up. Real receipts must not be added to the repository.

Each `<name>.pdf` has a `<name>.json` with the values the parser must extract.
`paid_at` is the printed date interpreted in the `Europe/Berlin` time zone.
The PDF creation date (metadata) is set 40 seconds after the payment, as on
real receipts, so the fallback for unreadable dates can be tested too.

| File | What it covers |
| --- | --- |
| `pace_super` | Afternoon payment, same structure as the original example |
| `pace_diesel_morning` | Single-digit month, day and hour; winter time (UTC+1); multi-word station name |
| `pace_super_e10_midnight` | Payment at 11:56 PM on December 31 (matching window crosses midnight and the year); multi-word fuel type |

## Regenerating

The PDFs are rendered by headless Chromium, like the real receipts. Edit
`RECEIPTS` in `generate.py` and run:

```sh
pip install pypdf
python generate.py --chromium /path/to/chromium
```
