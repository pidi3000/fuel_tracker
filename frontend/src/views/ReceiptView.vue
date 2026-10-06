<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getJson, postJson } from '../api'
import FuelUpForm from '../components/FuelUpForm.vue'
import { addressMapUrl, formatDate, formatDateTime, formatMoney, formatNumber } from '../format'
import { loadReference, reference } from '../reference'
import { showToast } from '../toast'
import type { FuelUp, Receipt, RecordCandidate } from '../types'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))

const receipt = ref<Receipt | null>(null)
const error = ref('')
const busy = ref(false)

// Fuel records in LubeLogger this receipt may belong to (entered by hand, so without a receipt)
const candidates = ref<RecordCandidate[]>([])
const lookingForRecords = ref(false)
const recordsError = ref('')
const includeOtherAmounts = ref(false)

async function loadCandidates() {
  lookingForRecords.value = true
  recordsError.value = ''
  try {
    candidates.value = await getJson<RecordCandidate[]>(
      `/receipts/${id.value}/record-candidates?include_other_amounts=${includeOtherAmounts.value}`,
    )
  } catch (e) {
    candidates.value = []
    recordsError.value = (e as Error).message
  } finally {
    lookingForRecords.value = false
  }
}

watch(includeOtherAmounts, () => void loadCandidates())

onMounted(async () => {
  try {
    receipt.value = await getJson<Receipt>(`/receipts/${id.value}`)
    await loadReference()
    if (receipt.value.state === 'unmatched' && receipt.value.paid_at) await loadCandidates()
  } catch (e) {
    error.value = (e as Error).message
  }
})

async function useRecord(candidate: RecordCandidate) {
  const what = `${candidate.vehicle_name}, ${formatDate(candidate.date)}`
  if (!confirm(`Attach this receipt to the fuel record in LubeLogger (${what})?`)) return
  busy.value = true
  error.value = ''
  try {
    await postJson(`/receipts/${id.value}/link-record`, {
      vehicle_id: candidate.vehicle_id,
      record_id: candidate.record_id,
    })
    showToast('The receipt is attached to the fuel record in LubeLogger.')
    await router.push({ name: 'home' })
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

function dayLabel(days: number): string {
  if (days === 0) return 'the same day'
  return days === 1 ? '1 day later' : `${days} days later`
}

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
                <span v-else-if="receipt.linked_record_id" class="muted">
                  (attached to fuel record #{{ receipt.linked_record_id }} in LubeLogger)
                </span>
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
        <section v-if="receipt.paid_at" class="card">
          <h2>Match to a fuel record in LubeLogger</h2>
          <p class="muted small">
            For a fuel-up you entered in LubeLogger yourself. The receipt is attached to that
            record, and nothing new is created. A record is dated the day of the receipt or later;
            the closest day comes first.
          </p>
          <label class="check">
            <input v-model="includeOtherAmounts" type="checkbox" /> Also show records with another
            amount or price
          </label>
          <p v-if="lookingForRecords" class="muted">Looking for fuel records…</p>
          <div v-else-if="recordsError" class="alert error" role="alert">{{ recordsError }}</div>
          <p v-else-if="!candidates.length" class="muted">
            No fuel record in LubeLogger fits this receipt.
          </p>
          <ul class="list">
            <li
              v-for="candidate in candidates"
              :key="`${candidate.vehicle_id}-${candidate.record_id}`"
            >
              <div class="item-head">
                <div>
                  <div class="item-title">{{ candidate.vehicle_name }}</div>
                  <div class="muted small">
                    {{ formatDate(candidate.date) }} ({{ dayLabel(candidate.days_after) }}) ·
                    {{ formatNumber(candidate.odometer, 0) }} km
                  </div>
                </div>
                <button
                  type="button"
                  class="primary"
                  :disabled="busy"
                  @click="useRecord(candidate)"
                >
                  Use this record
                </button>
              </div>
              <div class="small">
                <span :class="candidate.amount_matches ? 'ok-text' : 'warn-text'">
                  {{ formatNumber(candidate.fuel_consumed) }}
                  {{ reference.fuel?.volume_unit ?? '' }}
                  {{ candidate.amount_matches ? '✓' : '(other amount)' }}
                </span>
                ·
                <span :class="candidate.price_matches ? 'ok-text' : 'warn-text'">
                  {{ formatMoney(candidate.cost, reference.fuel?.currency ?? 'EUR') }}
                  {{ candidate.price_matches ? '✓' : '(other price)' }}
                </span>
              </div>
              <div v-if="candidate.notes" class="small muted">{{ candidate.notes }}</div>
            </li>
          </ul>
        </section>
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
