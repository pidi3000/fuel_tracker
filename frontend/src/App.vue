<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { getJson } from './api'

const version = ref<string>()

onMounted(async () => {
  try {
    version.value = (await getJson<{ version: string }>('/version')).version
  } catch {
    version.value = 'unknown'
  }
})
</script>

<template>
  <header>
    <h1>Fuel Tracker</h1>
  </header>
  <main>
    <p>Nothing here yet. Fuel-ups come in a later version.</p>
  </main>
  <footer>Version {{ version ?? '…' }}</footer>
</template>
