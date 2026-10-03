/** Small wrapper around fetch for the Fuel Tracker API. */

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | undefined

/** Called when a request that needed a login comes back 401 (e.g. the session expired). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler
}

/** Turns FastAPI's error bodies ("detail" as text or a list of validation errors) into text. */
function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = item.loc?.filter((part) => part !== 'body').join('.')
        const message = (item.msg ?? '').replace(/^Value error, /, '')
        return field ? `${field}: ${message}` : message
      })
      .join('; ')
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (response.status === 204) return undefined as T
  let data: unknown = null
  try {
    data = await response.json()
  } catch {
    // not JSON
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/login')) onUnauthorized?.()
    throw new ApiError(response.status, errorMessage(data, `Request failed (${response.status})`))
  }
  return data as T
}

export const getJson = <T>(path: string) => request<T>('GET', path)
export const postJson = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
export const patchJson = <T>(path: string, body: unknown) => request<T>('PATCH', path, body)
export const putJson = <T>(path: string, body: unknown) => request<T>('PUT', path, body)
export const deleteJson = <T = void>(path: string) => request<T>('DELETE', path)
