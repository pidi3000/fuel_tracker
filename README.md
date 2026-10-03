# Fuel Tracker

A small self-hosted web app that makes logging fuel-ups in
[LubeLogger](https://lubelogger.com) quick and mostly automatic.

## What is it?

LubeLogger keeps all your vehicles and fuel-ups, but typing in odometer, amount,
price, fuel type and location at the pump is tedious. Fuel Tracker does the
typing for you: you enter only what nothing else knows (vehicle, odometer
reading, location), and it fills in the rest and creates the fuel record in
LubeLogger.

## How does it work?

1. You log a fuel-up in the web UI (a phone-friendly PWA) or through the API,
   for example from an Apple Shortcut: vehicle, odometer reading and location.
2. Fuel type, amount, price and station come from the Pace Drive receipt email
   that arrives after paying. Fuel Tracker watches a mailbox for it and matches
   the receipt to your fuel-up by time. Without a receipt, enter these by hand.
3. The finished record, with the receipt PDF attached, is created in
   LubeLogger. If something fails or needs a look (no receipt arrived, a value
   couldn't be read), the fuel-up is flagged and you are notified.

It supports several users with admin and user roles, and every fuel-up can be
reviewed before it is sent. Details are in the
[functional outline](docs/functional-outline.md).

## Quick setup

### 1. LubeLogger

- Create two extra fields for **fuel records** (*Settings → Manage Extra
  Fields*, record type *Fuel*), both **not** required:

  | Name | Type |
  | --- | --- |
  | GPS Location | Location |
  | Address | Text |

- Create an API key with *Edit* permission on all vehicles (needed to check for
  duplicate receipts).

### 2. Docker

The image is `ghcr.io/pidi3000/fuel_tracker`. The repository is private, so log
in first with a GitHub token that has the `read:packages` scope:

```sh
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

1. Download [`docker-compose.yml`](docker-compose.yml) and
   [`.env.example`](.env.example) into an empty folder and rename the second
   file to `.env`.
2. Edit `.env`: set `LUBELOGGER_URL` and `LUBELOGGER_API_KEY`, and the `IMAP_*`
   values for the mailbox that receives the receipts (leave `IMAP_HOST` empty
   for manual fuel-ups only).
3. Start it with `docker compose up -d`.
4. Open `http://<server>:8000` and create the first admin account. All other
   settings are managed in the web UI.

Data lives in the `fuel-tracker-data` volume. To update, run
`docker compose pull && docker compose up -d`. Put the app behind an HTTPS
reverse proxy: browsers only share the GPS location on HTTPS.

## More

- [Functional outline](docs/functional-outline.md): everything the app does
- [Technical design](docs/technical-design.md) and
  [development](docs/development.md)
- [Apple Shortcut](docs/shortcut.md)
