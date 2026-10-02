# Fuel Tracker

A small self-hosted web server that makes logging fuel-ups into
[LubeLogger](https://lubelogger.com) quick and mostly automatic.

## Purpose

LubeLogger is the system of record for all vehicles and their fuel-ups. Entering
a fuel-up by hand means typing the odometer, liters, price, fuel type and
location every time, usually while standing at the pump. Most of that
information already exists elsewhere: the Pace Drive app knows what was paid,
for how much fuel, and at which station.

Fuel Tracker connects these systems. The user only provides what nothing else
knows (which vehicle and the odometer reading). The server collects the rest,
then creates the fuel record in LubeLogger.

## Connected systems

| System | Role |
| --- | --- |
| **LubeLogger** | Source of the vehicle list; destination for the finished fuel record |
| **Pace Drive API** | Payment details (amount, fuel type, total price, station address) for fuel-ups paid in the app |
| **Email inbox** | Dedicated mailbox that receives Pace Drive fuel receipts; used when the API route isn't available |
| **OCR** | Reads the emailed receipts, using a receipt template that is set up once |
| **Apple Shortcuts** | Alternative front end that starts a fuel-up from the phone, e.g. right after paying |

## What it does

### Web UI

A simple form for creating a fuel-up record:

- **Vehicle**: selected from the vehicles loaded from LubeLogger
- **Odometer reading**: required
- **Full fuel-up**: yes/no, defaults to *yes*
- **Date and time**: defaults to *now*
- **Payment info source**: Pace Drive API, Pace Drive email receipt, or manual

The same flow is also available to the Apple Shortcut workflow.

### Fuel-up record creation flow

1. The user starts a new fuel-up record.
2. The user enters the odometer reading. Other fields keep their defaults
   unless changed.
3. The user chooses where the payment information comes from:
   1. **Pace Drive API**: the server loads the transaction details from the
      Pace Drive API.
   2. **Pace Drive email receipt**:
      1. The server checks the connected inbox for the receipt.
      2. If it isn't there yet, the server retries after a while.
      3. Once it arrives, the server runs OCR on the receipt.
      4. The relevant values are extracted using the receipt template, which
         is set up once and defines where each value is on the receipt.
   3. **Manual**: the user enters:
      1. **Fuel type**: chosen from a configurable list (defaults: Diesel,
         Super, Super Plus, Super E10)
      2. **Fuel amount**: in the configured unit
      3. **Total price**
4. When all information is available, the server creates the fuel record in
   LubeLogger through its API. The Pace Drive and email routes can take a few
   minutes, so this step runs in the background.

### Location

Each fuel-up stores where it happened:

- **Manual entry**: the device's GPS location at the time of entry
- **Pace Drive (API or receipt)**: the station address provided by Pace Drive

## Configuration (overview)

- LubeLogger connection
- Pace Drive API access
- Email inbox access for receipts
- Receipt OCR template
- Fuel types (with defaults)
- Units (fuel volume, currency)
