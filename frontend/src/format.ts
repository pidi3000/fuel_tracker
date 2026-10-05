/** Number as written by the API ("23.000") for display, without needless zeros. */
export function formatNumber(value: string | number | null, maxDecimals = 3): string {
  if (value === null || value === '') return '–'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: maxDecimals })
}

export function formatMoney(value: string | null, currency: string): string {
  if (value === null) return '–'
  return Number(value).toLocaleString(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  })
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Dates are shown as ISO 8601 (2026-09-23, 17:08), whatever language the browser uses. */
function isoDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function isoTime(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDateTime(value: string | null): string {
  if (!value) return '–'
  const date = new Date(value)
  return `${isoDate(date)} ${isoTime(date)}`
}

export function formatTime(value: string | null): string {
  if (!value) return '–'
  return isoTime(new Date(value))
}

/** A date as the API sends it ("2026-09-23"); already ISO, so it is shown as it is. */
export function formatDate(value: string): string {
  return value.slice(0, 10)
}

/** The text in the date and time input: local time, "2026-09-23 17:08". */
export function toLocalInput(date: Date): string {
  return `${isoDate(date)} ${isoTime(date)}`
}

/** Reads the text of the date and time input. Returns null if it isn't a real date and time. */
export function parseLocalInput(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/.exec(value.trim())
  if (!match) return null
  const [year, month, day, hour, minute] = match.slice(1).map(Number) as [
    number,
    number,
    number,
    number,
    number,
  ]
  const date = new Date(year, month - 1, day, hour, minute)
  const real =
    date.getFullYear() === year &&
    date.getMonth() === month - 1 &&
    date.getDate() === day &&
    date.getHours() === hour &&
    date.getMinutes() === minute
  return real ? date : null
}

export function formatCoordinates(latitude: number | null, longitude: number | null): string {
  return latitude === null || longitude === null ? '–' : `${latitude}, ${longitude}`
}

/** A short headline for why a fuel-up needs attention (the API's `attention` code). */
export function attentionTitle(code: string | null): string {
  switch (code) {
    case 'review':
      return 'Waiting for your review'
    case 'unit_mismatch':
      return "Unit or currency doesn't match"
    case 'unreadable':
      return "Receipt values couldn't be read"
    case 'date_fallback':
      return 'Receipt date taken from the PDF'
    default:
      return 'Needs attention'
  }
}
