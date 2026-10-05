import { computed, ref } from 'vue'

export type Theme = 'light' | 'dark'

// index.html reads the same key to apply the theme before the page is drawn
const STORAGE_KEY = 'fuel-tracker:theme'
const systemQuery = window.matchMedia('(prefers-color-scheme: dark)')

function readChoice(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null // storage may be blocked
  }
}

/** The theme picked with the button. null: nothing picked, the system's theme is used. */
const choice = ref<Theme | null>(readChoice())
const systemTheme = ref<Theme>(systemQuery.matches ? 'dark' : 'light')

/** The theme in use right now. */
export const theme = computed<Theme>(() => choice.value ?? systemTheme.value)

function apply() {
  const root = document.documentElement
  if (choice.value) root.dataset.theme = choice.value
  else delete root.dataset.theme
}

/** Call once at start-up. */
export function initTheme(): void {
  systemQuery.addEventListener('change', (event) => {
    systemTheme.value = event.matches ? 'dark' : 'light'
  })
  apply()
}

/** Switches between light and dark. Switching to what the system uses anyway goes back to
 *  following the system, so a changed system theme is picked up again. */
export function toggleTheme(): void {
  const next: Theme = theme.value === 'dark' ? 'light' : 'dark'
  choice.value = next === systemTheme.value ? null : next
  try {
    if (choice.value) localStorage.setItem(STORAGE_KEY, choice.value)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // storage may be blocked; the theme still changes until the page is closed
  }
  apply()
}
