<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getJson, patchJson, postJson } from '../api'
import FuelUpForm from '../components/FuelUpForm.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { debounced, onEvent } from '../events'
import { attentionTitle, formatDateTime, formatMoney, formatNumber, formatTime } from '../format'
import { showToast } from '../toast'
import type { FuelUp, Receipt } from '../types'

const route = useRoute()
const id = computed(() => Number(route.params.id))

const fuelUp = ref<FuelUp | null>(null)
const receipt = ref<Receipt | null>(null)
const error = ref('')
const notFound = ref(false)
const editing = ref(false)
const busy = ref(false)

async function load() {
  try {
    fuelUp.value = await getJson<FuelUp>(`/fuel-ups/${id.value}`)
    receipt.value = fuelUp.value.receipt_id
      ? await getJson<Receipt>(`/receipts/${fuelUp.value.receipt_id}`)
      : null
    if (!fuelUp.value.editable) editing.value = false
    error.value = ''
  } catch (e) {
    const message = (e as Error).message
    if (message.includes('No such fuel-up')) notFound.value = true
    else error.value = message
  }
}

const reload = debounced(() => void load())
let stop: Array<() => void> = []
onMounted(() => {
  void load()
  stop = [
    onEvent('fuel_up', () => reload()),
    onEvent('receipt', () => reload()),
    onEvent('resync', () => reload()),
  ]
})
onBeforeUnmount(() => stop.forEach((fn) => fn()))

async function act(path: string, done: string) {
  busy.value = true
  error.value = ''
  try {
    fuelUp.value = await postJson<FuelUp>(`/fuel-ups/${id.value}/${path}`)
    showToast(done)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function save(payload: Record<string, unknown>) {
  busy.value = true
  error.value = ''
  try {
    fuelUp.value = await patchJson<FuelUp>(`/fuel-ups/${id.value}`, payload)
    editing.value = false
    showToast('Changes saved.')
    await load()
  } catch (e) {
    error.value = (e as Error).message
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } finally {
    busy.value = false
  }
}

const mapUrl = computed(() => {
  const f = fuelUp.value
  if (!f || f.latitude === null || f.longitude === null) return null
  return coordinatesMapUrl(f.latitude, f.longitude)
})

const paymentLabel = computed(() =>
  fuelUp.value?.payment_source === 'email_receipt' ? 'Pace Drive email receipt' : 'Manual',
)
</script>

<template>
  <main class="page">
    <RouterLink to="/" class="small">← All fuel-ups</RouterLink>

    <p v-if="notFound" class="muted">
      This fuel-up doesn't exist, or it was deleted after it was finished.
    </p>
    <template v-else-if="fuelUp">
      <div class="page-head">
        <h1>{{ fuelUp.vehicle_name }}</h1>
        <StatusBadge :fuel-up="fuelUp" />
      </div>

      <div v-if="error" class="alert error" role="alert">{{ error }}</div>

      <div v-if="fuelUp.status === 'needs_attention'" class="alert warn">
        <strong>{{ attentionTitle(fuelUp.attention) }}</strong
        ><br />{{ fuelUp.attention_message }}
      </div>
      <div v-if="fuelUp.status === 'failed'" class="alert error">
        <strong>Failed</strong><br />{{ fuelUp.error_message }}
      </div>
      <div v-if="fuelUp.sending" class="alert info">
        Sending to LubeLogger…
        <template v-if="fuelUp.error_message"> {{ fuelUp.error_message }}</template>
      </div>
      <div v-if="fuelUp.waiting_for_receipt" class="alert info">
        Waiting for the receipt email
        <template v-if="fuelUp.receipt_deadline">
          until {{ formatTime(fuelUp.receipt_deadline) }}</template
        >. A receipt within a few minutes of {{ formatDateTime(fuelUp.fuel_up_time) }} is used
        automatically.
      </div>
      <div v-if="fuelUp.status === 'done'" class="alert ok">
        In LubeLogger as fuel record #{{ fuelUp.lubelogger_record_id }}. This fuel-up is removed
        here after a while; the record stays in LubeLogger.
      </div>
      <div v-for="warning in fuelUp.warnings" :key="warning" class="alert info">
        {{ warning.replace(/^receipt: /, '') }}
      </div>

      <div class="actions" style="margin-bottom: 1rem">
        <button
          v-if="fuelUp.status === 'needs_attention'"
          class="primary"
          type="button"
          :disabled="busy"
          @click="act('approve', 'Approved. Sending to LubeLogger.')"
        >
          Approve and send
        </button>
        <button
          v-if="fuelUp.status === 'failed'"
          class="primary"
          type="button"
          :disabled="busy"
          @click="act('retry', 'Trying again.')"
        >
          {{
            fuelUp.payment_source === 'email_receipt' && !fuelUp.receipt_id
              ? 'Search for the receipt again'
              : 'Try again'
          }}
        </button>
        <button v-if="fuelUp.editable" type="button" @click="editing = !editing">
          {{ editing ? 'Cancel editing' : 'Edit' }}
        </button>
      </div>

      <FuelUpForm
        v-if="editing"
        :key="fuelUp.id"
        :fuel-up="fuelUp"
        submit-label="Save changes"
        :busy="busy"
        @submit="save"
      />

      <section v-else class="card">
        <table class="kv">
          <tbody>
            <tr>
              <th>Date and time</th>
              <td>{{ formatDateTime(fuelUp.fuel_up_time) }}</td>
            </tr>
            <tr>
              <th>Odometer</th>
              <td>{{ formatNumber(fuelUp.odometer, 0) }}</td>
            </tr>
            <tr>
              <th>Full fuel-up</th>
              <td>{{ fuelUp.is_fill_to_full ? 'Yes' : 'No' }}</td>
            </tr>
            <tr v-if="fuelUp.missed_fuel_up">
              <th>Missed fuel-up</th>
              <td>Yes</td>
            </tr>
            <tr>
              <th>Location</th>
              <td>
                <a v-if="mapUrl" :href="mapUrl" target="_blank" rel="noopener">
                  {{ fuelUp.latitude }}, {{ fuelUp.longitude }}
                </a>
                <span v-else class="muted">none</span>
              </td>
            </tr>
            <tr>
              <th>Payment</th>
              <td>{{ paymentLabel }}</td>
            </tr>
            <tr>
              <th>Fuel type</th>
              <td>{{ fuelUp.fuel_type ?? '–' }}</td>
            </tr>
            <tr>
              <th>Fuel amount</th>
              <td>
                {{
                  fuelUp.quantity ? `${formatNumber(fuelUp.quantity)} ${fuelUp.volume_unit}` : '–'
                }}
              </td>
            </tr>
            <tr>
              <th>Total price</th>
              <td>{{ formatMoney(fuelUp.total_price, fuelUp.currency) }}</td>
            </tr>
            <tr v-if="fuelUp.address">
              <th>Station</th>
              <td>{{ fuelUp.address }}</td>
            </tr>
            <tr v-if="receipt">
              <th>Receipt</th>
              <td>
                <RouterLink :to="{ name: 'receipt', params: { id: receipt.id } }"
                  >Details</RouterLink
                >
                <template v-if="receipt.has_pdf">
                  ·
                  <a :href="`/api/receipts/${receipt.id}/pdf`" target="_blank" rel="noopener"
                    >PDF</a
                  >
                </template>
              </td>
            </tr>
            <tr>
              <th>Created by</th>
              <td>{{ fuelUp.created_by }}, {{ formatDateTime(fuelUp.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </main>
</template>
