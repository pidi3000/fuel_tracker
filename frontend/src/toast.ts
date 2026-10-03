import { reactive } from 'vue'

export interface Toast {
  id: number
  kind: 'ok' | 'error' | 'info' | 'warn'
  text: string
}

export const toasts = reactive<Toast[]>([])
let nextId = 1

export function showToast(text: string, kind: Toast['kind'] = 'ok', milliseconds = 4000): void {
  const id = nextId++
  toasts.push({ id, kind, text })
  setTimeout(() => dismissToast(id), milliseconds)
}

export function dismissToast(id: number): void {
  const index = toasts.findIndex((t) => t.id === id)
  if (index >= 0) toasts.splice(index, 1)
}
