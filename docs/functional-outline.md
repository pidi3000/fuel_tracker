# Functional outline

What Fuel Tracker does, in detail. For a short introduction and the quick
setup, see the [README](../README.md).

## Purpose

LubeLogger is the system of record for all vehicles and their fuel-ups. Entering
a fuel-up by hand means typing the odometer, amount, price, fuel type and
location every time, usually while standing at the pump. Most of that
information already exists elsewhere: when paying with the Pace Drive app, a
receipt with the fuel type, amount, price and station address is emailed
afterwards.

Fuel Tracker connects these systems. The user only provides what nothing else
knows (vehicle, odometer reading, location). The server collects the rest and
then creates the fuel record in LubeLogger.

For how it is built, see the [technical design](technical-design.md); for
working on the code, see [development](development.md); the Apple Shortcut is
described in [shortcut](shortcut.md).

## Connected systems

| System | Role |
| --- | --- |
| **LubeLogger** | Source of the vehicle list and last odometer reading; destination for the finished fuel record |
| **Email inbox** | Dedicated mailbox that receives the Pace Drive receipts (PDF attachment). This is the primary source of payment data |
| **Pace Drive API** | Possible future source of payment data. Postponed until API access is confirmed |
| **Mail server (SMTP)** | Sends the notification emails (through [Apprise](https://github.com/caronc/apprise)). Optional: without it, notifications are only shown in the web UI |
| **Apple Shortcuts** | A client of the server's API, like the web UI. The server needs nothing Shortcut-specific |

## What it does

### Creating a fuel-up

Each fuel-up has these fields, entered in the web UI or sent through the API:

| Field | Notes |
| --- | --- |
| Vehicle | Chosen from the vehicles loaded from LubeLogger |
| Odometer reading | Required, whole number. Rejected if it is lower than the vehicle's last reading in LubeLogger or in a fuel-up that hasn't been sent to LubeLogger yet |
| Full fuel-up | Defaults to *yes* |
| Missed fuel-up | LubeLogger's flag. Defaults to *no* |
| Date and time | Defaults to *now*, in the device's local time. Typed or picked from a calendar, always in ISO format (`2026-10-05 17:08`: year first, 24-hour clock, weeks starting on Monday), whatever language the browser uses |
| GPS location | Requested from the browser (the server always runs on HTTPS) or sent by the Shortcut |
| Payment source | **Pace Drive email receipt** or **Manual** (the Pace Drive API is postponed). The web UI starts with the email receipt selected; the Shortcut asks, and the API defaults to *Manual* |
| Manual payment details | Only for *Manual*: fuel type (from the configured list), fuel amount and total price. The UI shows the configured units next to the inputs |

The web UI and the Shortcut both use the same API. It responds immediately:
either the fuel-up was created (the web UI shows a confirmation popup), or there
is an error, such as an invalid odometer reading. The fuel-up then stays
**Pending** until all data is collected and the record is created in
LubeLogger, or until it fails.

### Payment data from the email receipt

1. The server first checks receipts already in the inbox, in case the receipt
   arrived before the fuel-up was entered. Otherwise it waits for new mail.
   The mail server notifies it as soon as a mail arrives, so it doesn't need
   to check periodically.
2. A receipt matches a fuel-up when its date and time is within a configurable
   window of the fuel-up's time (default: 10 minutes). There is only one user,
   so no two fuel-ups are expected inside that window.
3. The receipt is a PDF with real text, so the values can be read directly.
   No OCR is needed for the current Pace Drive receipts. Extracted values:
   - Station name and address (e.g. *JET, Kanalstr. 46, 32545 Bad
     Oeynhausen*)
   - Date and time. This is plain text on the receipt (e.g.
     *9/23/2026, 5:08 PM*), read using the configured Pace Drive date format
     and time zone. If the printed date can't be read, the server falls back
     to the PDF's creation time (stored in the file's metadata, normally the
     same minute as the payment). When this backup is used, the fuel-up is
     still processed, but the user is notified and the fuel-up is marked
     with a warning in the overview
   - Fuel type (e.g. *Super*), used as-is: the configured fuel types use the
     same names as Pace Drive
   - Quantity and its unit (e.g. *23.00 L*)
   - Total price and currency (e.g. *54.03 EUR*)
   - Transaction ID, so the same receipt is never used for two fuel-ups. It
     is written to the LubeLogger notes, so this check still works after
     Fuel Tracker has deleted its own copy of the fuel-up. Before a receipt is
     used, all fuel records of all vehicles in LubeLogger are checked for its
     transaction ID, plus the fuel-ups still in progress in Fuel Tracker
4. If the receipt's units differ from the configured units, or a value can't
   be found on the receipt (for example, because Pace changed the layout), the
   fuel-up is set to **Needs attention** and the user is notified. Nothing is
   guessed. Automatic unit conversion may come later.
5. If no matching receipt arrives within the configurable wait time (default:
   60 minutes), the fuel-up is marked **Failed** and the user is notified.
6. An email stays in the inbox until its receipt is linked to a fuel-up (or
   ignored). Then it is marked as read and moved to a separate, configurable
   folder (default: *Processed*). The inbox only holds receipts that haven't
   been dealt with yet. If the same receipt arrives again (the same email
   twice, or the same receipt in another email), it is not used again. The
   admins are notified, and the email is flagged on the mail server and moved
   at once, so it stands out in the processed folder.

### Receipts without a fuel-up

A receipt that matches no fuel-up is listed in the web UI as an unmatched
receipt. The user can either:

- **Match it to a fuel record that already exists in LubeLogger**: for a fuel-up
  that was entered in LubeLogger by hand. The receipt is attached to that
  record and no new one is created (see below).
- **Complete it**: add the vehicle, odometer reading and other fuel-up fields.
  It is then processed like any other fuel-up.
- **Ignore it**: for example, when paying for a vehicle that isn't tracked in
  LubeLogger. The receipt is moved to the processed folder and no record is
  created.

#### Matching to an existing fuel record

The date decides. A receipt is always older than the record entered for it
(the receipt is made when paying, the record is entered afterwards, and
LubeLogger only keeps the day), so only records dated on the day of the receipt
or later are offered, up to 30 days after it, the closest day first. The fuel
amount and the total price are checked against the receipt as well, to the cent:
by default only records where both fit are shown, and a checkbox also shows the
other records of that period with their differences marked. Records that
already have a Pace Drive receipt are not offered.

Choosing a record adds to it, and keeps everything else as it is:

- the receipt PDF as an attachment,
- the same notes as a fuel-up sent by Fuel Tracker (fuel type, payment, the user
  who linked it and the line *PaceDrive Transaction ID: …*, so the receipt is
  never used twice). If the record already has notes, these are added below them
  after two empty lines, and
- the station and address in the *Address* extra field, if that is empty.

The receipt then counts as used: its email is marked as read and moved, like
for a fuel-up.

### Manual payment data

The user enters fuel type, fuel amount and total price. Fuel types come from the
settings, with defaults for Diesel, Super, Super Plus and Super E10. The fuel
amount and price use the configured units.

### Writing to LubeLogger

When all data is available, the server creates the fuel record in LubeLogger:

| LubeLogger field | Value |
| --- | --- |
| Date, odometer, fuel amount, cost | From the fuel-up and its payment data |
| Is fill to full, missed fuel-up | From the fuel-up |
| Notes | One item per line: fuel type, payment source, the Fuel Tracker user who created the fuel-up and, for receipts, the transaction ID (e.g. *Fuel type: Super* / *Payment: Pace Drive email receipt* / *Created by: alice* / *PaceDrive Transaction ID: b595859d-…*; manual entries have *Payment: Manual* and no transaction ID) |
| Extra field *GPS Location* | Raw GPS coordinates (e.g. *52.2063,8.8024*) |
| Extra field *Address* | Station name and address from the receipt (empty for manual entries). The station name may move to its own extra field or a tag later |
| Attachment | The receipt PDF (receipt fuel-ups only) |

A setting decides whether records are written right away or held for review
first. During testing, the user checks and corrects each fuel-up in the web UI
before it is sent. Later, records are written automatically.

### Fuel-up overview

LubeLogger is the only long-term store for fuel-ups. Fuel Tracker keeps a
fuel-up only while it is in progress, and deletes its copy once the record is
in LubeLogger. A short, configurable grace period (default: 7 days) keeps
finished fuel-ups visible with their details, which helps when checking that
everything worked.

The web UI shows two lists:

- **In progress** (the start page, below the receipts that have no fuel-up
  yet): fuel-ups stored in Fuel Tracker, with their status
- **History** (its own page): past fuel records loaded live from LubeLogger,
  for the vehicles the user has access to, optionally for one vehicle. This
  includes records entered directly in LubeLogger

| Status | Meaning |
| --- | --- |
| **Pending** | Waiting for the receipt |
| **Needs attention** | Waiting for review, paused because of a unit mismatch, or a receipt value couldn't be read |
| **Done** | Created in LubeLogger. Removed from Fuel Tracker after the grace period |
| **Failed** | No receipt arrived in time, or LubeLogger rejected the record or stayed unreachable after several retries |

Until a fuel-up is sent to LubeLogger (status Pending, Needs attention or
Failed), the user can edit all its fields, such as a mistyped odometer reading.
Until then it can also be deleted (there is a *Delete* button on the fuel-up).
Nothing has reached LubeLogger, so nothing there changes; a receipt the fuel-up
held goes back to the receipts without a fuel-up. A fuel-up that is being sent
right now, or that is Done, can't be deleted: once a fuel-up is in LubeLogger,
changes are made there.

For a failed fuel-up, the user can also retry the receipt search or enter the
payment data manually. Notifications are only sent when something fails or needs
attention, never on success.

### Notifications

A notification appears in the web UI (the bell in the header), and, when email is
set up, is also sent by email. Each notification is of one *kind*, and each user
chooses on the **Account** page which kinds they want by email:

| Kind | When | Who can get it |
| --- | --- | --- |
| A fuel-up failed | No receipt arrived in time, or LubeLogger refused the fuel-up or couldn't be reached | everyone |
| A fuel-up needs attention | A receipt value couldn't be read or doesn't match the units, or its date came from the PDF | everyone |
| A fuel-up is ready for review | The receipt arrived and the fuel-up waits for approval (*Review before sending*) | everyone |
| A receipt has no fuel-up | A receipt matches no fuel-up | admins |
| A receipt email has a problem | A receipt email couldn't be read, or turned up again | admins |
| A new version is available | A newer image of the app exists | admins |

All kinds are on until a user switches them off. The email goes to:

- **the user it is about**, for example the one who created a fuel-up that
  failed, if that user has saved an email address and wants this kind. A user who
  switched the kind off gets nothing, and the admins don't get it in their place;
- **the admins**, when the user has no email address, and for everything that
  isn't about one user (the *admins* kinds above). Every admin with an email
  address who wants the kind gets it, once.

Everyone sets their own email address on the Account page (admins can't set
other users' addresses). If nobody who should get a notification has an address
or wants it by email, it is only shown in the web UI.

The email has the subject *Fuel Tracker:* followed by the title of the
notification, and the same text, followed by a link to the page it is about (the
fuel-up, the receipt, the settings for an update, otherwise the notifications).
The link is built from the address the app is reached at (`PUBLIC_URL`); without
it, the emails have no link. If the mail server can't be reached, sending is tried
again after 1, 5 and 15 minutes; then the email is given up (the notification
stays in the web UI). Emails are only sent for notifications from now on:
switching email on, or updating, never sends old ones.

The Account page also has a button that sends a test email to the saved address.
The admins see on the settings page whether email notifications are set up and
working. The mail server is set up with `APPRISE_EMAIL_URL` (see
[Settings](#settings-overview)).

### Navigation and appearance

The start page (the fuel-ups in progress) is the main page, and *New fuel-up* is
a button on it. The other pages (history, notifications, account, and for admins
users and settings) are in the header on a wide screen, and in a menu behind the
menu button on a narrow one such as a phone, where the menu button also shows
the number of unread notifications.

A button in the header switches between the light and the dark theme. Until it
is used, the app follows the device's setting. The choice is remembered in the
browser, and switching back to the device's theme goes back to following it.

### Users and access

The server supports multiple user accounts with two roles:

- **Admin**: manages settings and users, and decides which vehicles each user
  can access
- **User**: creates and views fuel-ups for the vehicles assigned to them

Every user manages their own account: password, API tokens, email address and
which notifications they get by email.

The server is only reachable through a VPN and sits behind a reverse proxy, but
it still handles login itself. The Shortcut uses the same API with its own
credentials.

## LubeLogger setup

Before using Fuel Tracker, create these extra fields for **fuel records** in
LubeLogger (*Settings → Manage Extra Fields*, record type *Fuel*):

| Name | Type | Required | Content |
| --- | --- | --- | --- |
| GPS Location | Location | No | Raw GPS coordinates, in the same *latitude,longitude* format that LubeLogger's own location button uses |
| Address | Text | No | Station name and postal address from the receipt |

Leave *Required* off: manual fuel-ups have no address, and a required field
would block editing those records in LubeLogger later. The field names can be
changed, as long as the same names are entered in the Fuel Tracker settings.

Fuel Tracker also needs a LubeLogger API key with *Edit* permission on all
vehicles. Access to every vehicle is needed so the duplicate-receipt check sees
all fuel records. Which vehicles each Fuel Tracker user can log for is
controlled in Fuel Tracker.

## Settings (overview)

- LubeLogger connection and API key, plus the names of the two extra fields
  (defaults: *GPS Location*, *Address*)
- Email inbox access, and the folder for processed receipts (default
  *Processed*)
- Matching window between fuel-up and receipt (default 10 minutes)
- How long to wait for a receipt before failing (default 60 minutes)
- Time zone (e.g. Europe/Berlin)
- Pace Drive receipt date format (default: the English app format, e.g.
  *9/23/2026, 5:08 PM*)
- Review before sending to LubeLogger: on/off
- How long finished fuel-ups stay in Fuel Tracker (default 7 days)
- Fuel types (defaults: Diesel, Super, Super Plus, Super E10)
- Units for fuel amount and currency (default: liters and EUR, matching
  LubeLogger)
- Email notifications: the mail server (an Apprise email URL; an environment variable only, as it holds the password) and the address the app is reached at, for the link in the emails (`PUBLIC_URL`). Each user sets their own email address and choices on the Account page
- Users, roles and vehicle access

### Update check

The app looks daily whether a newer image of itself is available and tells the
admins (settings page and a notification). A release image only looks for newer
release images, and a test image only for a newer test image. Updating itself
is not done: pull the new image and restart.

## Later

- Pace Drive API as a payment source
- Automatic unit conversion
- Converting between GPS coordinates and postal addresses
- Notifications through other Apprise targets (ntfy, Pushover, Telegram, …), besides email
