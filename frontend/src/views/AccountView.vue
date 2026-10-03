<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { deleteJson, getJson, postJson } from '../api'
import { auth } from '../auth'
import type { ApiToken, ApiTokenCreated } from '../types'

const tokens = ref<ApiToken[]>([])
const tokenName = ref('')
const newToken = ref<ApiTokenCreated | null>(null)
const tokenError = ref('')

const currentPassword = ref('')
const newPassword = ref('')
const repeatPassword = ref('')
const passwordError = ref('')
const passwordDone = ref(false)

async function loadTokens() {
  tokens.value = await getJson<ApiToken[]>('/tokens')
}

onMounted(loadTokens)

async function createToken() {
  tokenError.value = ''
  try {
    newToken.value = await postJson<ApiTokenCreated>('/tokens', { name: tokenName.value })
    tokenName.value = ''
    await loadTokens()
  } catch (e) {
    tokenError.value = (e as Error).message
  }
}

async function revoke(token: ApiToken) {
  if (!confirm(`Revoke the token "${token.name}"? Clients using it stop working.`)) return
  await deleteJson(`/tokens/${token.id}`)
  if (newToken.value?.id === token.id) newToken.value = null
  await loadTokens()
}

async function changePassword() {
  passwordError.value = ''
  passwordDone.value = false
  if (newPassword.value !== repeatPassword.value) {
    passwordError.value = 'The new passwords do not match.'
    return
  }
  try {
    await postJson('/auth/password', {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    currentPassword.value = newPassword.value = repeatPassword.value = ''
    passwordDone.value = true
  } catch (e) {
    passwordError.value = (e as Error).message
  }
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'never'
}
</script>

<template>
  <main class="page">
    <h1>Account</h1>

    <section class="card">
      <h2>{{ auth.user?.username }}</h2>
      <p class="muted">
        {{ auth.user?.role === 'admin' ? 'Admin' : 'User'
        }}<template v-if="auth.user?.email"> · {{ auth.user.email }}</template>
      </p>
    </section>

    <section class="card">
      <h2>API tokens</h2>
      <p class="muted">
        A token lets a client such as the Apple Shortcut create fuel-ups as you. Send it as
        <code>Authorization: Bearer &lt;token&gt;</code>.
      </p>

      <div v-if="newToken" class="alert ok">
        <p><strong>Copy the token now.</strong> It is only shown once.</p>
        <div class="token-box">{{ newToken.token }}</div>
      </div>

      <table v-if="tokens.length">
        <thead>
          <tr>
            <th>Name</th>
            <th>Last used</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="token in tokens" :key="token.id">
            <td>{{ token.name }}</td>
            <td class="muted small">{{ formatDate(token.last_used_at) }}</td>
            <td><button class="danger" type="button" @click="revoke(token)">Revoke</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">No tokens yet.</p>

      <form class="row" @submit.prevent="createToken">
        <div class="field">
          <label for="token-name">New token</label>
          <input
            id="token-name"
            v-model="tokenName"
            placeholder="e.g. iPhone Shortcut"
            required
            maxlength="100"
          />
        </div>
        <div class="field" style="justify-content: flex-end; flex: 0 0 auto">
          <button type="submit">Create token</button>
        </div>
      </form>
      <div v-if="tokenError" class="alert error">{{ tokenError }}</div>
    </section>

    <form class="card" @submit.prevent="changePassword">
      <h2>Change password</h2>
      <div v-if="passwordError" class="alert error" role="alert">{{ passwordError }}</div>
      <div v-if="passwordDone" class="alert ok">
        Password changed. Other browsers were signed out.
      </div>
      <div class="field">
        <label for="current">Current password</label>
        <input
          id="current"
          v-model="currentPassword"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>
      <div class="field">
        <label for="new">New password</label>
        <input
          id="new"
          v-model="newPassword"
          type="password"
          autocomplete="new-password"
          required
          minlength="8"
        />
      </div>
      <div class="field">
        <label for="repeat">Repeat new password</label>
        <input
          id="repeat"
          v-model="repeatPassword"
          type="password"
          autocomplete="new-password"
          required
        />
      </div>
      <button type="submit">Change password</button>
    </form>
  </main>
</template>
