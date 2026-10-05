<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getJson, postJson } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { debounced, live, onEvent } from '../events'
import {
  attentionTitle,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatTime,
} from '../format'
import { loadReference, reference, vehicleLabel } from '../reference'
import { showToast } from '../toast'
import type { FuelUp, HistoryRecord, Receipt } from '../types'

const PAGE_SIZE = 20

const fuelUps = ref<FuelUp[]>([])
const receipts = ref<Receipt[]>([])
const history = ref<HistoryRecord[]>([])
const historyTotal = ref(0)
const historyError = ref('')
// The vehicle the history is limited to; empty shows all vehicles
const historyVehicle = ref('')

function historyQuery(limit: number, offset = 0): string {
  const vehicle = historyVehicle.value ? `&vehicle_id=${historyVehicle.value}` : ''
  return `/history?limit=${limit}&offset=${offset}${vehicle}`
}
const error = ref('')
const loaded = ref(false)
const loadingMore = ref(false)

const order = { needs_attention: 0, failed: 1, pending: 2, done: 3 }
const sorted = computed(() =>
  [...fuelUps.value].sort(
    (a, b) =>
      order[a.status] - order[b.status] ||
      new Date(b.fuel_up_time).getTime() - new Date(a.fuel_up_time).getTime(),
  ),
)

async function loadProgress() {
  try {
    fuelUps.value = await getJson<FuelUp[]>('/fuel-ups')
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadReceipts() {
  try {
    receipts.value = await getJson<Receipt[]>('/receipts')
  } catch {
    // the list is optional
  }
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

async function ignoreReceipt(receipt: Receipt) {
  if (!confirm('Ignore this receipt? No fuel-up is created for it.')) return
  try {
    await postJson(`/receipts/${receipt.id}/ignore`)
    showToast('Receipt ignored.', 'info')
    await loadReceipts()
  } catch (e) {
    showToast((e as Error).message, 'error')
  }
}

const reloadProgress = debounced(() => void loadProgress())
const reloadReceipts = debounced(() => void loadReceipts())
// A finished fuel-up shows up in the history a moment later
async function changeHistoryVehicle() {
  // Start from the first page of the other vehicle
  history.value = []
  historyTotal.value = 0
  await loadHistory()
}

const reloadHistory = debounced(() => void loadHistory(), 1000)

let stop: Array<() => void> = []

onMounted(async () => {
  await Promise.all([loadReference(), loadProgress(), loadReceipts(), loadHistory()])
  loaded.value = true
  stop = [
    onEvent('fuel_up', () => {
      reloadProgress()
      reloadHistory()
    }),
    onEvent('receipt', () => {
      reloadReceipts()
      reloadProgress()
    }),
    onEvent('resync', () => {
      reloadProgress()
      reloadReceipts()
      reloadHistory()
    }),
  ]
})

onBeforeUnmount(() => stop.forEach((fn) => fn()))

const unit = computed(() => reference.fuel?.volume_unit ?? '')
const currency = computed(() => reference.fuel?.currency ?? 'EUR')
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>Fuel-ups</h1>
      <RouterLink class="btn primary" to="/new">New fuel-up</RouterLink>
    </div>

    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

    <section class="card">
      <h2>In progress</h2>
      <p v-if="loaded && !sorted.length" class="muted">
        Nothing is on its way to LubeLogger right now.
      </p>
      <ul class="list">
        <li v-for="fuelUp in sorted" :key="fuelUp.id">
          <div class="item-head">
            <div>
              <RouterLink class="item-title" :to="{ name: 'fuel-up', params: { id: fuelUp.id } }">
                {{ fuelUp.vehicle_name }}
              </RouterLink>
              <div class="muted small">
                {{ formatDateTime(fuelUp.fuel_up_time) }} ·
                {{ formatNumber(fuelUp.odometer, 0) }} km
              </div>
            </div>
            <StatusBadge :fuel-up="fuelUp" />
          </div>
          <div v-if="fuelUp.fuel_type" class="small">
            {{ fuelUp.fuel_type }} · {{ formatNumber(fuelUp.quantity) }} {{ fuelUp.volume_unit }} ·
            {{ formatMoney(fuelUp.total_price, fuelUp.currency) }}
          </div>
          <div v-if="fuelUp.waiting_for_receipt" class="small muted">
            Waiting for the receipt email<template v-if="fuelUp.receipt_deadline">
              (until {{ formatTime(fuelUp.receipt_deadline) }})</template
            >.
          </div>
          <div v-if="fuelUp.status === 'needs_attention'" class="small warn-text">
            <strong>{{ attentionTitle(fuelUp.attention) }}:</strong>
            {{ fuelUp.attention_message }}
          </div>
          <div v-if="fuelUp.error_message" class="small error-text">
            {{ fuelUp.error_message }}
          </div>
        </li>
      </ul>
    </section>

    <section v-if="receipts.length" class="card">
      <h2>Receipts without a fuel-up</h2>
      <ul class="list">
        <li v-for="receipt in receipts" :key="receipt.id">
          <div class="item-head">
            <div>
              <RouterLink class="item-title" :to="{ name: 'receipt', params: { id: receipt.id } }">
                {{ receipt.station ?? 'Unreadable receipt' }}
              </RouterLink>
              <div class="muted small">
                <template v-if="receipt.paid_at">{{ formatDateTime(receipt.paid_at) }}</template>
                <template v-if="receipt.fuel_type">
                  · {{ receipt.fuel_type }} · {{ formatNumber(receipt.quantity) }}
                  {{ receipt.unit }} ·
                  {{ formatMoney(receipt.total, receipt.currency ?? currency) }}
                </template>
              </div>
              <div v-if="receipt.parse_error" class="small error-text">
                {{ receipt.parse_error }}
              </div>
            </div>
            <div class="actions">
              <RouterLink
                v-if="receipt.paid_at"
                class="btn"
                :to="{ name: 'receipt', params: { id: receipt.id } }"
              >
                Complete
              </RouterLink>
              <button type="button" @click="ignoreReceipt(receipt)">Ignore</button>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <section class="card">
      <div class="item-head">
        <h2>History</h2>
        <span class="small muted" :title="live.connected ? 'Updates arrive live' : 'Not connected'">
          {{ historyTotal }} in LubeLogger
        </span>
      </div>
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
          <div v-if="record.address" class="small muted">{{ record.address }}</div>
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
