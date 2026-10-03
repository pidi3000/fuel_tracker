import { reactive } from 'vue'

import { getJson, postJson, setUnauthorizedHandler } from './api'
import type { User } from './types'

export const auth = reactive({
  loaded: false,
  needsSetup: false,
  user: null as User | null,
})

/** Finds out whether the first-run setup is needed and who is signed in. */
export async function loadAuth(): Promise<void> {
  const { needs_setup } = await getJson<{ needs_setup: boolean }>('/setup')
  auth.needsSetup = needs_setup
  auth.user = null
  if (!needs_setup) {
    try {
      auth.user = await getJson<User>('/auth/me')
    } catch {
      // not signed in
    }
  }
  auth.loaded = true
}

export async function login(username: string, password: string): Promise<void> {
  auth.user = await postJson<User>('/auth/login', { username, password })
}

export async function setup(username: string, password: string, email: string): Promise<void> {
  auth.user = await postJson<User>('/setup', { username, password, email: email || null })
  auth.needsSetup = false
}

export async function logout(): Promise<void> {
  try {
    await postJson('/auth/logout')
  } finally {
    auth.user = null
  }
}

export function useCurrentUser(): User {
  if (!auth.user) throw new Error('not signed in')
  return auth.user
}

// A request answered with 401 means the session is gone: show the login page
export function installSessionExpiryHandler(onExpired: () => void): void {
  setUnauthorizedHandler(() => {
    if (auth.user) {
      auth.user = null
      onExpired()
    }
  })
}
