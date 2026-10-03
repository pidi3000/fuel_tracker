import { reactive } from 'vue'

export type LocationStatus = 'idle' | 'locating' | 'ok' | 'denied' | 'unavailable'

/** The device location for a fuel-up: asked for once, and again on request. */
export function useGeolocation() {
  const state = reactive({
    status: 'idle' as LocationStatus,
    latitude: null as number | null,
    longitude: null as number | null,
    accuracy: null as number | null,
  })

  function locate(): void {
    if (!('geolocation' in navigator)) {
      state.status = 'unavailable'
      return
    }
    state.status = 'locating'
    navigator.geolocation.getCurrentPosition(
      (position) => {
        state.latitude = Number(position.coords.latitude.toFixed(6))
        state.longitude = Number(position.coords.longitude.toFixed(6))
        state.accuracy = Math.round(position.coords.accuracy)
        state.status = 'ok'
      },
      (error) => {
        state.status = error.code === error.PERMISSION_DENIED ? 'denied' : 'unavailable'
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
    )
  }

  function clear(): void {
    state.latitude = state.longitude = state.accuracy = null
    state.status = 'idle'
  }

  return { state, locate, clear }
}
