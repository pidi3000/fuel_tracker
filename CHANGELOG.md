# Changelog

Notes for each release, written for the person running Fuel Tracker. Technical
details are linked from each release on GitHub.

## Unreleased

### New

- Accounts: the first start shows a setup page that creates the admin account;
  after that, users sign in with username and password. There are two roles
  (admin and user), and admins decide which vehicles each user may log for.
- API tokens for the Apple Shortcut: create and revoke them under *Account*.
- Manual fuel-ups: choose a vehicle from LubeLogger, enter the odometer reading,
  fuel type, amount and price, and the location of the device is recorded.
  The fuel-up is written to LubeLogger in the background and retried if
  LubeLogger is not reachable. The Apple Shortcut uses the same API
  (`POST /api/fuel-ups`).
- The odometer reading must not be lower than the last one in LubeLogger or in
  another fuel-up that is still on its way.
- Optional review step: with `REVIEW_BEFORE_SEND=true` (the default), every
  fuel-up waits for your approval before it is sent to LubeLogger.
- Errors (for example a rejected record) are shown in the app.
- Pace Drive receipts by email: choose *Pace Drive email receipt* as the
  payment source and the fuel type, amount, price and station address are read
  from the receipt PDF that Pace Drive emails to the receipt mailbox. The
  receipt is matched to the fuel-up by its time (10 minutes by default) and
  attached to the fuel record in LubeLogger. The mailbox is watched live (IMAP
  IDLE).
- A fuel-up that has waited 60 minutes for its receipt fails and you are told;
  you can retry the search or enter the payment data by hand.
- Receipts without a fuel-up are kept in a list, where you can complete or
  ignore them. A receipt is never used twice (its transaction ID is saved in
  the LubeLogger notes).
- Receipts in another unit or currency than LubeLogger's, or with values that
  can't be read, wait for your attention instead of being sent.

- Overview page that updates by itself, without reloading: fuel-ups in
  progress with their status, receipts without a fuel-up, and the history of
  your fuel records, loaded from LubeLogger (so it includes records you enter
  there directly).
- Fuel-up page: see all details and messages, approve a fuel-up that waits for
  review, retry a failed one, and edit it until it is in LubeLogger. A fuel-up
  that waits for its receipt can be switched to manual entry.
- Notifications (bell in the header): failures, receipts that need attention
  and receipts without a fuel-up, also shown as a message when they happen.
- Receipts without a fuel-up can be completed (vehicle and odometer reading)
  or ignored in the app, and the receipt PDF can be opened.

- Admins can add, edit and remove users in the app and decide which vehicles
  each user may log fuel-ups for.
- Settings page for admins: shows whether LubeLogger and the receipt mailbox
  work, and lets you change the review step, matching window, receipt wait
  time, time zone, date format, fuel types, units and the keep-time of finished
  fuel-ups without restarting. A saved value wins over the environment
  variable until you reset it.
- Finished fuel-ups are deleted after 7 days (setting), together with their
  receipt. The fuel record stays in LubeLogger and in the history.

### Breaking changes / upgrade notes

- To use email receipts, set `IMAP_HOST`, `IMAP_USER` and `IMAP_PASSWORD` for
  the mailbox that receives the Pace Drive receipts. Processed receipts are
  moved to a folder called `Processed` (created if missing); set
  `IMAP_PROCESSED_FOLDER` to change it.
- Set `LUBELOGGER_URL` (and `LUBELOGGER_API_KEY` if LubeLogger requires login)
  in `.env`.
- In LubeLogger, create two extra fields for fuel records (*Settings*, *Manage
  Extra Fields*, type *Fuel*): `GPS Location` (type *Location*) and `Address`
  (type *Text*). Fuel Tracker writes the location and, later, the station
  address there. See the README for details.

- First Docker image: an empty web UI that shows the app version, and the
  `/api/health` endpoint used by the Docker health check. No fuel-up features
  yet.
