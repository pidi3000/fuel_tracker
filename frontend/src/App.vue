<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { getJson } from './api'
import { auth, installSessionExpiryHandler, logout } from './auth'
import ThemeToggle from './components/ThemeToggle.vue'
import ToastList from './components/ToastList.vue'
import { live, startEvents, stopEvents } from './events'
import { notifications, startNotifications, stopNotifications } from './notifications'

const router = useRouter()
const version = ref<string>()

// On a narrow screen the pages are in a menu behind the menu button
const header = ref<HTMLElement | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const menuOpen = ref(false)

function closeMenu(returnFocus = false) {
  if (!menuOpen.value) return
  menuOpen.value = false
  if (returnFocus) menuButton.value?.focus()
}

function closeMenuOnOutsideClick(event: PointerEvent) {
  if (!header.value?.contains(event.target as Node)) closeMenu()
}

onMounted(() => document.addEventListener('pointerdown', closeMenuOnOutsideClick))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeMenuOnOutsideClick))
watch(
  () => router.currentRoute.value.fullPath,
  () => closeMenu(),
)

installSessionExpiryHandler(() => {
  void router.replace({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
})

// Live updates run while someone is signed in
watch(
  () => auth.user?.id,
  (id) => {
    if (id) {
      startEvents()
      startNotifications()
    } else {
      stopNotifications()
      stopEvents()
    }
  },
  { immediate: true },
)

onMounted(async () => {
  try {
    version.value = (await getJson<{ version: string }>('/version')).version
  } catch {
    version.value = 'unknown'
  }
})

async function signOut() {
  await logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <header ref="header" class="app-header" @keydown.esc="closeMenu(true)">
    <div class="inner">
      <RouterLink class="brand" to="/">Fuel Tracker</RouterLink>
      <template v-if="auth.user">
        <nav id="main-menu" :class="{ open: menuOpen }" aria-label="Main" @click="closeMenu()">
          <RouterLink to="/">Fuel-ups</RouterLink>
          <RouterLink to="/history">History</RouterLink>
          <RouterLink to="/notifications" class="bell">
            Notifications<span v-if="notifications.unread" class="count">{{
              notifications.unread
            }}</span>
          </RouterLink>
          <RouterLink to="/account">Account</RouterLink>
          <template v-if="auth.user.role === 'admin'">
            <RouterLink to="/admin/users">Users</RouterLink>
            <RouterLink to="/admin/settings">Settings</RouterLink>
          </template>
          <button class="link" type="button" @click="signOut">Sign out</button>
        </nav>
        <span
          class="live-dot"
          :class="{ on: live.connected }"
          :title="live.connected ? 'Updates arrive live' : 'Reconnecting…'"
        ></span>
      </template>
      <ThemeToggle />
      <button
        v-if="auth.user"
        ref="menuButton"
        class="icon-button menu-button"
        type="button"
        aria-controls="main-menu"
        :aria-expanded="menuOpen"
        :aria-label="
          notifications.unread ? `Menu, ${notifications.unread} unread notifications` : 'Menu'
        "
        @click="menuOpen = !menuOpen"
      >
        <svg
          viewBox="0 0 24 24"
          width="20"
          height="20"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          aria-hidden="true"
        >
          <path v-if="menuOpen" d="M6 6l12 12M18 6L6 18" />
          <path v-else d="M4 7h16M4 12h16M4 17h16" />
        </svg>
        <span v-if="notifications.unread && !menuOpen" class="count">{{
          notifications.unread
        }}</span>
      </button>
    </div>
  </header>
  <ToastList />
  <RouterView />
  <footer class="app-footer">Fuel Tracker {{ version ?? '…' }}</footer>
</template>
