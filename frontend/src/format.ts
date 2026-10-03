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

export function formatDateTime(value: string | null): string {
  if (!value) return '–'
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Value for an <input type="datetime-local"> (local time, to the minute). */
export function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function fromLocalInput(value: string): string {
  return new Date(value).toISOString()
}

export function formatTime(value: string | null): string {
  if (!value) return '–'
  return new Date(value).toLocaleTimeString(undefined, { timeStyle: 'short' })
}

/** A date as the API sends it ("2026-09-23"), without shifting it by the time zone. */
export function formatDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year!, month! - 1, day).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

export function formatCoordinates(latitude: number | null, longitude: number | null): string {
  return latitude === null || longitude === null ? '–' : `${latitude}, ${longitude}`
}
