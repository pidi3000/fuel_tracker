<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { postJson } from '../api'
import FuelUpForm from '../components/FuelUpForm.vue'
import { showToast } from '../toast'
import type { FuelUp } from '../types'

const router = useRouter()
const busy = ref(false)
const error = ref('')

async function create(payload: Record<string, unknown>) {
  error.value = ''
  busy.value = true
  try {
    const fuelUp = await postJson<FuelUp>('/fuel-ups', payload)
    showToast('Fuel-up created. It is being sent to LubeLogger.')
    await router.push({ name: 'home', query: { created: String(fuelUp.id) } })
  } catch (e) {
    error.value = (e as Error).message
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page">
    <h1>New fuel-up</h1>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <FuelUpForm submit-label="Create fuel-up" :busy="busy" auto-locate @submit="create" />
  </main>
</template>
