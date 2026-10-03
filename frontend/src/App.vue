<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { getJson } from './api'
import { auth, installSessionExpiryHandler, logout } from './auth'
import ToastList from './components/ToastList.vue'
import { live, startEvents, stopEvents } from './events'
import { notifications, startNotifications, stopNotifications } from './notifications'

const router = useRouter()
const version = ref<string>()

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
  <header class="app-header">
    <div class="inner">
      <RouterLink class="brand" to="/">Fuel Tracker</RouterLink>
      <template v-if="auth.user">
        <nav>
          <RouterLink to="/">Fuel-ups</RouterLink>
          <RouterLink to="/new">New</RouterLink>
          <RouterLink to="/notifications" class="bell">
            Notifications<span v-if="notifications.unread" class="count">{{
              notifications.unread
            }}</span>
          </RouterLink>
          <RouterLink to="/account">Account</RouterLink>
        </nav>
        <span
          class="live-dot"
          :class="{ on: live.connected }"
          :title="live.connected ? 'Updates arrive live' : 'Reconnecting…'"
        ></span>
        <button class="link" type="button" @click="signOut">Sign out</button>
      </template>
    </div>
  </header>
  <ToastList />
  <RouterView />
  <footer class="app-footer">Fuel Tracker {{ version ?? '…' }}</footer>
</template>
