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

### Breaking changes / upgrade notes

- Set `LUBELOGGER_URL` (and `LUBELOGGER_API_KEY` if LubeLogger requires login)
  in `.env`.
- In LubeLogger, create two extra fields for fuel records (*Settings*, *Manage
  Extra Fields*, type *Fuel*): `GPS Location` (type *Location*) and `Address`
  (type *Text*). Fuel Tracker writes the location and, later, the station
  address there. See the README for details.

- First Docker image: an empty web UI that shows the app version, and the
  `/api/health` endpoint used by the Docker health check. No fuel-up features
  yet.
