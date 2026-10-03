import { reactive } from 'vue'

/** Live updates: the server tells the browser (Server-Sent Events) when something changed. */
export const live = reactive({ connected: false })

type EventType = 'fuel_up' | 'receipt' | 'notification' | 'resync'
type Handler = () => void

const handlers = new Map<EventType, Set<Handler>>()
let source: EventSource | undefined

function emit(type: EventType): void {
  handlers.get(type)?.forEach((handler) => handler())
}

/** Runs `handler` for each event of this type. Returns a function that stops it.
 *  "resync" is sent when the connection is (re)established: events may have been missed. */
export function onEvent(type: EventType, handler: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set())
  handlers.get(type)!.add(handler)
  return () => handlers.get(type)?.delete(handler)
}

export function startEvents(): void {
  if (source) return
  source = new EventSource('/api/events')
  source.addEventListener('hello', () => {
    live.connected = true
    emit('resync')
  })
  for (const type of ['fuel_up', 'receipt', 'notification'] as const) {
    source.addEventListener(type, () => emit(type))
  }
  // The browser reconnects by itself
  source.onerror = () => {
    live.connected = false
  }
}

export function stopEvents(): void {
  source?.close()
  source = undefined
  live.connected = false
}

/** A function that runs `fn` once after calls stop coming in for `wait` milliseconds. */
export function debounced(fn: () => void, wait = 250): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  return () => {
    clearTimeout(timer)
    timer = setTimeout(fn, wait)
  }
}
