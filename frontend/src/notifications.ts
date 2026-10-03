import { reactive } from 'vue'

import { getJson, postJson } from './api'
import { onEvent } from './events'
import { showToast } from './toast'
import type { AppNotification } from './types'

export const notifications = reactive({
  unread: 0,
  latestId: 0,
})

const toastKind = { info: 'info', warning: 'warn', error: 'error' } as const

/** Fetches the unread notifications; announces ones that arrived since the last look. */
export async function refreshNotifications(announce = true): Promise<void> {
  const unread = await getJson<AppNotification[]>('/notifications?unread=true')
  notifications.unread = unread.length
  const fresh = unread.filter((n) => n.id > notifications.latestId).reverse()
  if (announce && notifications.latestId > 0) {
    for (const n of fresh) {
      showToast(n.title, toastKind[n.level], n.level === 'error' ? 10000 : 6000)
    }
  }
  notifications.latestId = Math.max(notifications.latestId, ...unread.map((n) => n.id))
}

export async function markAllRead(): Promise<void> {
  await postJson('/notifications/read', {})
  notifications.unread = 0
}

let stop: Array<() => void> = []

export function startNotifications(): void {
  stopNotifications()
  // The first load only counts, so old messages don't pop up as toasts
  void refreshNotifications(false).catch(() => undefined)
  stop = [
    onEvent('notification', () => void refreshNotifications().catch(() => undefined)),
    onEvent('resync', () => void refreshNotifications().catch(() => undefined)),
  ]
}

export function stopNotifications(): void {
  stop.forEach((fn) => fn())
  stop = []
  notifications.unread = 0
  notifications.latestId = 0
}
