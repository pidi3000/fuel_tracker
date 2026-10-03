# Changelog

Notes for each release, written for the person running Fuel Tracker. Technical
details are linked from each release on GitHub.

## Unreleased

### New

- Accounts: the first start shows a setup page that creates the admin account;
  after that, users sign in with username and password. There are two roles
  (admin and user), and admins decide which vehicles each user may log for.
- API tokens for the Apple Shortcut: create and revoke them under *Account*.

- First Docker image: an empty web UI that shows the app version, and the
  `/api/health` endpoint used by the Docker health check. No fuel-up features
  yet.
