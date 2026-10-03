<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { getJson } from './api'
import { auth, installSessionExpiryHandler, logout } from './auth'

const router = useRouter()
const version = ref<string>()

installSessionExpiryHandler(() => {
  void router.replace({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
})

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
          <RouterLink to="/account">Account</RouterLink>
        </nav>
        <button class="link" type="button" @click="signOut">Sign out</button>
      </template>
    </div>
  </header>
  <RouterView />
  <footer class="app-footer">Fuel Tracker {{ version ?? '…' }}</footer>
</template>
