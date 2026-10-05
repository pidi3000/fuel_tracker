<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getJson, postJson } from '../api'
import FuelUpForm from '../components/FuelUpForm.vue'
import { addressMapUrl, formatDateTime, formatMoney, formatNumber } from '../format'
import { showToast } from '../toast'
import type { FuelUp, Receipt } from '../types'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))

const receipt = ref<Receipt | null>(null)
const error = ref('')
const busy = ref(false)

onMounted(async () => {
  try {
    receipt.value = await getJson<Receipt>(`/receipts/${id.value}`)
  } catch (e) {
    error.value = (e as Error).message
  }
})

async function complete(payload: Record<string, unknown>) {
  busy.value = true
  error.value = ''
  try {
    const fuelUp = await postJson<FuelUp>(`/receipts/${id.value}/complete`, payload)
    showToast('Fuel-up created from the receipt.')
    await router.push({ name: 'fuel-up', params: { id: fuelUp.id } })
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function ignore() {
  if (!confirm('Ignore this receipt? No fuel-up is created for it.')) return
  try {
    await postJson(`/receipts/${id.value}/ignore`)
    showToast('Receipt ignored.', 'info')
    await router.push({ name: 'home' })
  } catch (e) {
    error.value = (e as Error).message
  }
}
</script>

<template>
  <main class="page">
    <RouterLink to="/" class="small">← All fuel-ups</RouterLink>
    <h1>Receipt</h1>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

    <template v-if="receipt">
      <div v-if="receipt.parse_error" class="alert error">{{ receipt.parse_error }}</div>
      <div v-else-if="receipt.missing.length" class="alert warn">
        Couldn't read: {{ receipt.missing.join(', ') }}.
      </div>
      <div v-if="receipt.warnings.includes('date_from_pdf_metadata')" class="alert info">
        The printed date couldn't be read, so the time the PDF was created is used.
      </div>

      <section class="card">
        <table class="kv">
          <tbody>
            <tr>
              <th>Station</th>
              <td>
                <a
                  v-if="receipt.address"
                  :href="addressMapUrl(receipt.address)"
                  target="_blank"
                  rel="noopener"
                  >{{ receipt.address }}</a
                >
                <template v-else>{{ receipt.station ?? '–' }}</template>
              </td>
            </tr>
            <tr>
              <th>Time</th>
              <td>{{ formatDateTime(receipt.paid_at) }}</td>
            </tr>
            <tr>
              <th>Fuel type</th>
              <td>{{ receipt.fuel_type ?? '–' }}</td>
            </tr>
            <tr>
              <th>Amount</th>
              <td>
                {{ receipt.quantity ? `${formatNumber(receipt.quantity)} ${receipt.unit}` : '–' }}
              </td>
            </tr>
            <tr>
              <th>Total</th>
              <td>{{ receipt.currency ? formatMoney(receipt.total, receipt.currency) : '–' }}</td>
            </tr>
            <tr>
              <th>Transaction</th>
              <td class="small">{{ receipt.transaction_id ?? '–' }}</td>
            </tr>
            <tr>
              <th>State</th>
              <td>
                {{ receipt.state }}
                <RouterLink
                  v-if="receipt.fuel_up_id"
                  :to="{ name: 'fuel-up', params: { id: receipt.fuel_up_id } }"
                >
                  (fuel-up #{{ receipt.fuel_up_id }})
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="receipt.has_pdf">
          <a :href="`/api/receipts/${receipt.id}/pdf`" target="_blank" rel="noopener"
            >Open the PDF</a
          >
        </p>
      </section>

      <template v-if="receipt.state === 'unmatched'">
        <template v-if="receipt.paid_at">
          <h2>Create a fuel-up</h2>
          <p class="muted">
            Add what the receipt doesn't tell: the vehicle and the odometer reading. The time, fuel
            type, amount, price and station come from the receipt.
          </p>
          <FuelUpForm
            variant="receipt"
            submit-label="Create fuel-up"
            :busy="busy"
            @submit="complete"
          />
        </template>
        <p v-else class="muted">
          This receipt can't become a fuel-up because its time couldn't be read.
        </p>
        <button type="button" @click="ignore">Ignore this receipt</button>
      </template>
    </template>
  </main>
</template>
