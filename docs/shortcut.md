# Apple Shortcut

The Shortcut uses the same API as the web UI, so there is nothing
Shortcut-specific on the server. This page describes the request it sends.

## Set up

1. In the web UI, open *Account* and create an API token (for example
   "iPhone Shortcut"). Copy it; it is only shown once.
2. In the Shortcut, ask for the vehicle, the odometer reading and the payment
   method, get the current location, and add a *Get contents of URL* action as
   below.

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
| `payment_source` | no | `manual` (default) |
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
