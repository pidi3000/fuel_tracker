<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getJson } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatDateTime, formatMoney, formatNumber, formatTime } from '../format'
import type { FuelUp } from '../types'

const fuelUps = ref<FuelUp[]>([])
const loaded = ref(false)
const error = ref('')

async function load() {
  try {
    fuelUps.value = await getJson<FuelUp[]>('/fuel-ups')
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loaded.value = true
  }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>Fuel-ups</h1>
      <RouterLink class="btn primary" to="/new">New fuel-up</RouterLink>
    </div>

    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

    <section class="card">
      <p v-if="loaded && !fuelUps.length" class="muted">No fuel-ups in progress.</p>
      <ul class="list">
        <li v-for="fuelUp in fuelUps" :key="fuelUp.id">
          <div class="item-head">
            <div>
              <div class="item-title">{{ fuelUp.vehicle_name }}</div>
              <div class="muted small">
                {{ formatDateTime(fuelUp.fuel_up_time) }} ·
                {{ formatNumber(fuelUp.odometer, 0) }} km
              </div>
            </div>
            <StatusBadge :fuel-up="fuelUp" />
          </div>
          <div class="small">
            {{ fuelUp.fuel_type ?? '–' }} · {{ formatNumber(fuelUp.quantity) }}
            {{ fuelUp.volume_unit }} ·
            {{ formatMoney(fuelUp.total_price, fuelUp.currency) }}
          </div>
          <div v-if="fuelUp.waiting_for_receipt" class="small muted">
            Waiting for the receipt email<template v-if="fuelUp.receipt_deadline">
              (until {{ formatTime(fuelUp.receipt_deadline) }})</template
            >.
          </div>
          <div v-if="fuelUp.error_message" class="small" style="color: var(--danger)">
            {{ fuelUp.error_message }}
          </div>
        </li>
      </ul>
    </section>
  </main>
</template>
