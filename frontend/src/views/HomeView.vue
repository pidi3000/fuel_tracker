<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getJson, postJson } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { debounced, onEvent } from '../events'
import { attentionTitle, formatDateTime, formatMoney, formatNumber, formatTime } from '../format'
import { loadReference, reference } from '../reference'
import { showToast } from '../toast'
import type { FuelUp, Receipt } from '../types'

const fuelUps = ref<FuelUp[]>([])
const receipts = ref<Receipt[]>([])
const error = ref('')
const loaded = ref(false)

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
let stop: Array<() => void> = []

onMounted(async () => {
  await Promise.all([loadReference(), loadProgress(), loadReceipts()])
  loaded.value = true
  stop = [
    onEvent('fuel_up', reloadProgress),
    onEvent('receipt', () => {
      reloadReceipts()
      reloadProgress()
    }),
    onEvent('resync', () => {
      reloadProgress()
      reloadReceipts()
    }),
  ]
})

onBeforeUnmount(() => stop.forEach((fn) => fn()))

const currency = computed(() => reference.fuel?.currency ?? 'EUR')
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>Fuel-ups</h1>
      <RouterLink class="btn primary" to="/new">New fuel-up</RouterLink>
    </div>

    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

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
  </main>
</template>
