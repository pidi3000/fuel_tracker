# Apple Shortcut

The Shortcut adds a fuel-up from your iPhone while you stand at the pump. It
uses the same API as the web UI, so there is nothing Shortcut-specific on the
server.

It asks for the vehicle (with the last odometer reading as a hint), the
odometer reading and how you pay. For a manual fuel-up it also asks for the
fuel type, amount and price. It sends the location of the phone, then shows
*Fuel-up created* (with what the server says about it, for example that it
waits for your review) or the reason the server gave.

## Install

The file is `shortcut/Fuel Tracker.shortcut`. iOS only imports signed
Shortcuts, and signing is done with the `shortcuts` command on a Mac:

1. In the web UI, open *Account* and create an API token (for example
   "iPhone Shortcut"). Copy it; it is only shown once.
2. On a Mac, sign the file and send the result to the iPhone (AirDrop, iCloud
   Drive):

   ```sh
   shortcuts sign --mode anyone --input "shortcut/Fuel Tracker.shortcut" \
       --output ~/Desktop/"Fuel Tracker.shortcut"
   ```

3. Open it on the iPhone and tap *Add Shortcut*. It asks for the address of
   your server (`https://fuel.example.com`, without a slash at the end) and the
   token.
4. Run it once. iOS asks to share the location and to allow sending data to
   your server; allow both.

The token is stored inside the Shortcut on your phone, so don't share the
Shortcut after setting it up. To replace it, edit the second *Text* action.

Without a Mac, build it by hand:

1. *Text* with your server address, *Set Variable* `Server`. The same for the
   token, `Token`.
2. *Get Current Location*, then *Get Details of Locations* for *Latitude* and
   again for *Longitude*. Run each through *Replace Text*, `,` with `.`.
3. *Get Contents of URL*: `Server`/api/vehicles, header `Authorization` with
   `Bearer ` and `Token`.
4. *Repeat with Each* vehicle: *Get Dictionary from Input*, *Get Dictionary
   Value* `name`, *Add to Variable* `Names`. Then *Choose from List* `Names`.
5. *Repeat with Each* vehicle again: *If* its `name` is the choice, *Get
   Dictionary Value* `id` into `Vehicle ID`.
6. *Ask for Input* of type *Number* for the odometer reading.
7. *Choose from Menu*: *Pace Drive email receipt* and *Manual*. For *Manual*,
   get `/api/fuel-types`, *Choose from List* its `fuel_types`, and ask for the
   amount and the price (*Number*).
8. *Get Contents of URL*: method `POST`, `/api/fuel-ups`, the `Authorization`
   header, request body *JSON* with the fields below. Set the type of
   `vehicle_id` and `odometer` to *Number*. Send `latitude`, `longitude`,
   `quantity` and `total_price` as *Text*, each run through *Replace Text*
   (`,` with `.`) first: as *Number*, a phone set to German loses the decimal
   comma on the way (52,34 arrives as 5234).
9. *Get Dictionary Value* `id` of the answer. *If* it has a value, show *Fuel-up
   created*; otherwise *Show Alert* with the answer's `detail`.

## Changing the Shortcut

The source is `shortcut/fuel-tracker.cherri`, for the
[Cherri](https://cherrilang.org) compiler (`brew install
electrikmilk/cherri/cherri`). After changing it, run `python3
shortcut/build.py`, which rewrites `shortcut/Fuel Tracker.shortcut` (unsigned,
nothing is sent anywhere), and sign it as above. A test checks that the
Shortcut still sends fields the API knows.

## The request

```
POST https://<your server>/api/fuel-ups
Authorization: Bearer ft_...
Content-Type: application/json
```

```json
{
  "vehicle_id": 1,
  "odometer": 12345,
  "latitude": 52.2063,
  "longitude": 8.8024
}
```

The vehicle IDs are the ones LubeLogger uses. `GET /api/vehicles` (same
`Authorization` header) lists the vehicles you may log for, to fill the choice
menu in the Shortcut.

### Fields

| Field | Required | Notes |
| --- | --- | --- |
| `vehicle_id` | yes | A vehicle you have access to |
| `odometer` | yes | Whole number. Must not be lower than the last reading |
| `latitude`, `longitude` | no | Send both or neither |
| `fuel_up_time` | no | ISO 8601, e.g. `2026-09-23T17:08:00+02:00`. Defaults to now. Without a time zone it is read in the configured time zone |
| `is_fill_to_full` | no | Default `true` |
| `missed_fuel_up` | no | Default `false` |
| `payment_source` | no | `manual` (default), or `email_receipt` to read the payment data from the Pace Drive receipt |
| `fuel_type`, `quantity`, `total_price` | for `manual` | A fuel type from `GET /api/fuel-types`, the amount in the configured unit, the price in the configured currency |

## The answer

- **`201`**: the fuel-up was created. The body describes it, including its
  `status`. It is written to LubeLogger in the background; the web UI shows the
  progress.
- **`401`**: the token is wrong or was revoked.
- **`422`**: something is wrong with the data. `detail` says what, for example
  that the odometer reading is lower than the last one. Show it to the user.
- **`503`**: LubeLogger can't be reached and the server doesn't know the
  vehicles yet. Try again later.

In the Shortcut, show *Fuel-up created* on `201` and the `detail` text
otherwise.
