# Changelog

Notes for each release, written for the person running Fuel Tracker. Technical
details are linked from each release on GitHub.

## Unreleased

### New

- Dates and times are shown in ISO format (2026-10-05, 17:08), whatever language the browser
  uses, and the date and time of a new fuel-up is typed in that format.
- The station address from a receipt is now a link that opens it on OpenStreetMap, like the
  coordinates (on the fuel-up page, the receipt page and in the history).
- A fuel-up that needs attention now says why, with a short headline in the list and on
  the fuel-up page (for example "Waiting for your review", because *Review before
  sending* is turned on, or "Unit or currency doesn't match"). Failed fuel-ups are
  labelled with their reason in the same way.

## 0.1.0 - 2026-10-03

### Breaking changes / upgrade notes

This is the first version. To set it up:

- Set `LUBELOGGER_URL` (and `LUBELOGGER_API_KEY` if LubeLogger requires login)
  in `.env`.
- In LubeLogger, create two extra fields for fuel records (*Settings*, *Manage
  Extra Fields*, type *Fuel*): `GPS Location` (type *Location*) and `Address`
  (type *Text*). Fuel Tracker writes the location and the station address
  there. The *Connections* box on the settings page tells you if they are
  missing.
- To use email receipts, set `IMAP_HOST`, `IMAP_USER` and `IMAP_PASSWORD` for
  the mailbox that receives the Pace Drive receipts. Processed receipts are
  moved to a folder called `Processed` (created if missing); set
  `IMAP_PROCESSED_FOLDER` to change it.
- Open the app once: the setup page creates the admin account.

### Breaking changes / upgrade notes

This is the first version. To set it up:

- Set `LUBELOGGER_URL` (and `LUBELOGGER_API_KEY` if LubeLogger requires login)
  in `.env`.
- In LubeLogger, create two extra fields for fuel records (*Settings*, *Manage
  Extra Fields*, type *Fuel*): `GPS Location` (type *Location*) and `Address`
  (type *Text*). Fuel Tracker writes the location and the station address
  there. The *Connections* box on the settings page tells you if they are
  missing.
- To use email receipts, set `IMAP_HOST`, `IMAP_USER` and `IMAP_PASSWORD` for
  the mailbox that receives the Pace Drive receipts. Processed receipts are
  moved to a folder called `Processed` (created if missing); set
  `IMAP_PROCESSED_FOLDER` to change it.
- Open the app once: the setup page creates the admin account.

### New

- **Accounts:** users sign in with username and password. There are two roles
  (admin and user), and admins add users and decide which vehicles each user
  may log fuel-ups for. API tokens for the Apple Shortcut are created and
  revoked under *Account*.
- **Manual fuel-ups:** choose a vehicle from LubeLogger, enter the odometer
  reading, fuel type, amount and price; the location of the device is
  recorded. The fuel-up is written to LubeLogger in the background and retried
  if LubeLogger is not reachable. The Apple Shortcut uses the same API
  (`POST /api/fuel-ups`, see `docs/shortcut.md`).
- **Pace Drive email receipts:** choose *Pace Drive email receipt* as the
  payment source, and the fuel type, amount, price and station address are read
  from the receipt PDF that Pace Drive emails to the receipt mailbox, which is
  watched live. The receipt is matched to the fuel-up by its time (10 minutes
  by default) and attached to the fuel record in LubeLogger. A receipt is never
  used twice.
- **Careful by default:** the odometer reading must not be lower than the last
  one. Receipts in another unit or currency than LubeLogger's, or with values
  that can't be read, wait for your attention instead of being sent. With
  `REVIEW_BEFORE_SEND=true` (the default), every fuel-up waits for your
  approval before it is sent to LubeLogger.
- **Failures are reported:** a fuel-up that waited 60 minutes for its receipt,
  or that LubeLogger rejected, fails and you are told; you can retry or enter
  the payment data by hand.
- **Overview that updates by itself:** fuel-ups in progress with their status,
  receipts without a fuel-up (complete or ignore them), and the history of your
  fuel records loaded from LubeLogger. Open a fuel-up to see its details and to
  approve, retry or edit it until it is in LubeLogger.
- **Notifications:** a bell in the header and a message when something needs
  your attention.
- **Settings page for admins:** whether LubeLogger and the receipt mailbox
  work, and the review step, matching window, receipt wait time, time zone, date
  format, fuel types, units and keep-time of finished fuel-ups, changeable
  without a restart.
- Finished fuel-ups are deleted after 7 days (setting), together with their
  receipt. The fuel record stays in LubeLogger and in the history.
