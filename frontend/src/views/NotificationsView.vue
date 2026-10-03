<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getJson } from '../api'
import { formatDateTime } from '../format'
import { markAllRead } from '../notifications'
import type { AppNotification } from '../types'

const items = ref<AppNotification[]>([])
const loaded = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    items.value = await getJson<AppNotification[]>('/notifications')
    // The items keep their "new" look until the page is left
    await markAllRead()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loaded.value = true
  }
})

const kinds = { info: '', warning: 'warn', error: 'error' } as const
</script>

<template>
  <main class="page">
    <h1>Notifications</h1>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <section class="card">
      <p v-if="loaded && !items.length" class="muted">Nothing to report.</p>
      <ul class="list">
        <li v-for="item in items" :key="item.id" :class="{ unread: !item.is_read }">
          <div class="item-head">
            <div>
              <span class="badge" :class="kinds[item.level]">{{ item.level }}</span>
              <strong> {{ item.title }}</strong>
            </div>
            <span class="muted small">{{ formatDateTime(item.created_at) }}</span>
          </div>
          <div class="small">{{ item.message }}</div>
          <RouterLink
            v-if="item.fuel_up_id"
            class="small"
            :to="{ name: 'fuel-up', params: { id: item.fuel_up_id } }"
          >
            Open the fuel-up
          </RouterLink>
        </li>
      </ul>
    </section>
  </main>
</template>
