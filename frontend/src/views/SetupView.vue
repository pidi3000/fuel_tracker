<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { setup } from '../auth'

const router = useRouter()
const username = ref('')
const email = ref('')
const password = ref('')
const repeat = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  if (password.value !== repeat.value) {
    error.value = 'The passwords do not match.'
    return
  }
  busy.value = true
  try {
    await setup(username.value, password.value, email.value)
    await router.replace({ name: 'home' })
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page narrow">
    <h1>Welcome to Fuel Tracker</h1>
    <p class="muted">
      Create the first account. It becomes the admin and can add more users later.
    </p>
    <form class="card" @submit.prevent="submit">
      <div v-if="error" class="alert error" role="alert">{{ error }}</div>
      <div class="field">
        <label for="username">Username</label>
        <input
          id="username"
          v-model="username"
          autocomplete="username"
          required
          minlength="3"
          autofocus
        />
      </div>
      <div class="field">
        <label for="email">Email (optional)</label>
        <input id="email" v-model="email" type="email" autocomplete="email" />
        <span class="hint">Used for notifications in a later version.</span>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input
          id="password"
          v-model="password"
          type="password"
          autocomplete="new-password"
          required
          minlength="8"
        />
        <span class="hint">At least 8 characters.</span>
      </div>
      <div class="field">
        <label for="repeat">Repeat password</label>
        <input id="repeat" v-model="repeat" type="password" autocomplete="new-password" required />
      </div>
      <button class="primary" type="submit" :disabled="busy">Create admin account</button>
    </form>
  </main>
</template>
