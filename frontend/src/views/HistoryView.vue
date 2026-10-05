<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { getJson } from '../api'
import { debounced, live, onEvent } from '../events'
import { addressMapUrl, formatDate, formatMoney, formatNumber } from '../format'
import { loadReference, reference, vehicleLabel } from '../reference'
import type { HistoryRecord } from '../types'

const PAGE_SIZE = 20

const history = ref<HistoryRecord[]>([])
const historyTotal = ref(0)
const historyError = ref('')
// The vehicle the history is limited to; empty shows all vehicles
const historyVehicle = ref('')
const loaded = ref(false)
const loadingMore = ref(false)

function historyQuery(limit: number, offset = 0): string {
  const vehicle = historyVehicle.value ? `&vehicle_id=${historyVehicle.value}` : ''
  return `/history?limit=${limit}&offset=${offset}${vehicle}`
}

async function loadHistory() {
  try {
    // Reload as many records as are shown now, so "load more" isn't undone by an update
    const limit = Math.max(PAGE_SIZE, history.value.length)
    const page = await getJson<{ items: HistoryRecord[]; total: number }>(
      historyQuery(Math.min(limit, 100)),
    )
    history.value = page.items
    historyTotal.value = page.total
    historyError.value = ''
  } catch (e) {
    historyError.value = (e as Error).message
  }
}

async function loadMore() {
  loadingMore.value = true
  try {
    const page = await getJson<{ items: HistoryRecord[]; total: number }>(
      historyQuery(PAGE_SIZE, history.value.length),
    )
    history.value = [...history.value, ...page.items]
    historyTotal.value = page.total
  } catch (e) {
    historyError.value = (e as Error).message
  } finally {
    loadingMore.value = false
  }
}

async function changeHistoryVehicle() {
  // Start from the first page of the other vehicle
  history.value = []
  historyTotal.value = 0
  await loadHistory()
}

// A finished fuel-up shows up in the history a moment later
const reloadHistory = debounced(() => void loadHistory(), 1000)

let stop: Array<() => void> = []

onMounted(async () => {
  await Promise.all([loadReference(), loadHistory()])
  loaded.value = true
  stop = [onEvent('fuel_up', reloadHistory), onEvent('resync', reloadHistory)]
})

onBeforeUnmount(() => stop.forEach((fn) => fn()))

const unit = computed(() => reference.fuel?.volume_unit ?? '')
const currency = computed(() => reference.fuel?.currency ?? 'EUR')
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>History</h1>
      <span class="small muted" :title="live.connected ? 'Updates arrive live' : 'Not connected'">
        {{ historyTotal }} in LubeLogger
      </span>
    </div>

    <section class="card">
      <div v-if="reference.vehicles.length > 1" class="field">
        <label for="history-vehicle">Vehicle</label>
        <select id="history-vehicle" v-model="historyVehicle" @change="changeHistoryVehicle">
          <option value="">All vehicles</option>
          <option v-for="vehicle in reference.vehicles" :key="vehicle.id" :value="vehicle.id">
            {{ vehicleLabel(vehicle) }}
          </option>
        </select>
      </div>
      <div v-if="historyError" class="alert error" role="alert">{{ historyError }}</div>
      <p v-else-if="loaded && !history.length" class="muted">No fuel records found.</p>
      <ul class="list">
        <li v-for="record in history" :key="`${record.vehicle_id}-${record.id}`">
          <div class="item-head">
            <div>
              <div class="item-title">{{ record.vehicle_name }}</div>
              <div class="muted small">
                {{ formatDate(record.date) }} · {{ formatNumber(record.odometer, 0) }} km
              </div>
            </div>
            <div class="small">
              {{ formatNumber(record.fuel_consumed) }} {{ unit }} ·
              {{ formatMoney(record.cost, currency) }}
            </div>
          </div>
          <div v-if="record.files.length" class="small">
            <template v-for="(file, index) in record.files" :key="index">
              <template v-if="index"> · </template>
              <a
                :href="`/api/history/${record.vehicle_id}/${record.id}/files/${index}`"
                target="_blank"
                rel="noopener"
                >{{ file.name }}</a
              >
            </template>
          </div>
          <div v-if="record.address" class="small muted">
            <a :href="addressMapUrl(record.address)" target="_blank" rel="noopener">{{
              record.address
            }}</a>
          </div>
        </li>
      </ul>
      <button
        v-if="history.length < historyTotal"
        type="button"
        :disabled="loadingMore"
        @click="loadMore"
      >
        Load more
      </button>
    </section>
  </main>
</template>
