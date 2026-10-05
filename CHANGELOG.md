# Changelog

Notes for each release, written for the person running Fuel Tracker. Technical
details are linked from each release on GitHub.

## Unreleased

### New

- `RECEIPT_SENDER` can list several addresses, separated by commas, for example to test with
  receipts sent from another email account.
- A new fuel-up now starts with *Pace Drive email receipt* as the payment, so you only need
  the vehicle and the odometer reading. Choose *Enter it manually* to type the fuel type,
  amount and price yourself. (The Apple Shortcut still asks.)
- On a phone and other narrow screens, the pages are in a menu behind the menu button in the
  header (it also shows how many notifications are unread). The separate *New* link is gone:
  *New fuel-up* is the button on the start page. On a wide screen the pages stay in the
  header.
- A button in the header switches between the light and the dark theme. Until you use it, the
  app follows your device; your choice is remembered in the browser.
- The date and time of a fuel-up can be picked from a calendar (the button next to the field),
  as well as typed. The calendar follows ISO as well: the year comes first, weeks start on
  Monday, and the time is on a 24-hour clock, whatever language the browser uses.
- The history has its own page (*History* in the menu). On the start page, the receipts
  without a fuel-up are now at the top, above the fuel-ups in progress.

### Fixed

- Receipt emails now stay in the inbox until their receipt is linked to a fuel-up (or
  ignored), as described, and are then marked as read and moved. Before, they were moved as
  soon as they arrived, so an email without a fuel-up yet was already out of the inbox.

## 0.2.0 - 2026-10-05

### New

- An Apple Shortcut adds a fuel-up from your iPhone: it asks for the vehicle, the odometer
  reading and how you pay, and sends your location. It is `shortcut/Fuel Tracker.shortcut`;
  `docs/shortcut.md` explains how to sign (on a Mac) and install it.
- The app looks once a day whether a newer image of itself is available and shows it on the
  settings page, with a notification for the admins. A release image only looks for newer
  releases, and a test image only for a newer test image. Turn it off with `UPDATE_CHECK=false`.
- When a receipt turns up again (the same email twice, or the same receipt in another email),
  you now get a notification. It is not used again, and its email is flagged on the mail
  server before it is moved to the processed folder.
- Receipt emails are marked as read when they are moved to the processed folder.
- Dates and times are shown in ISO format (2026-10-05, 17:08), whatever language the browser
  uses, and the date and time of a new fuel-up is typed in that format.
- Files attached to a record in LubeLogger, such as the receipt, are linked in the history
  and open straight from Fuel Tracker.
- The history can be filtered by vehicle.
- When you add a fuel-up, the location is a link that opens it on OpenStreetMap, so you can
  check right away that it is roughly right.
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
