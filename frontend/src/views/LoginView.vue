<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { login } from '../auth'

const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await login(username.value, password.value)
    const next =
      typeof route.query.next === 'string' && route.query.next.startsWith('/')
        ? route.query.next
        : '/'
    await router.replace(next)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page narrow">
    <h1>Sign in</h1>
    <form class="card" @submit.prevent="submit">
      <div v-if="error" class="alert error" role="alert">{{ error }}</div>
      <div class="field">
        <label for="username">Username</label>
        <input id="username" v-model="username" autocomplete="username" required autofocus />
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input
          id="password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>
      <button class="primary" type="submit" :disabled="busy">Sign in</button>
    </form>
  </main>
</template>
