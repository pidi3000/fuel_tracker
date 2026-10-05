<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { getJson } from '../api'
import { coordinatesMapUrl, formatNumber, parseLocalInput, toLocalInput } from '../format'
import { useGeolocation } from '../geolocation'
import { loadReference, reference, vehicleLabel } from '../reference'
import type { FuelUp } from '../types'

const props = withDefaults(
  defineProps<{
    /** The fuel-up to edit; leave out to create one. */
    fuelUp?: FuelUp
    submitLabel: string
    busy?: boolean
    /** Locate the device when the form opens (for new fuel-ups). */
    autoLocate?: boolean
    /** "receipt": turn a receipt into a fuel-up (no time and payment, they come from the receipt). */
    variant?: 'full' | 'receipt'
  }>(),
  { fuelUp: undefined, variant: 'full' },
)

const emit = defineEmits<{ submit: [payload: Record<string, unknown>] }>()

const LAST_VEHICLE_KEY = 'fuel-tracker:last-vehicle'

const editing = computed(() => props.fuelUp !== undefined)
// A new fuel-up is paid for with Pace Drive in most cases, so its email receipt is the default
const originalSource = props.fuelUp?.payment_source ?? 'email_receipt'

const vehicleId = ref<number | ''>(props.fuelUp?.vehicle_id ?? '')
const odometer = ref<string>(props.fuelUp ? String(props.fuelUp.odometer) : '')
const time = ref(toLocalInput(props.fuelUp ? new Date(props.fuelUp.fuel_up_time) : new Date()))
const fillToFull = ref(props.fuelUp?.is_fill_to_full ?? true)
const missed = ref(props.fuelUp?.missed_fuel_up ?? false)
const paymentSource = ref<'manual' | 'email_receipt'>(originalSource)
const fuelType = ref(props.fuelUp?.fuel_type ?? '')
const quantity = ref(props.fuelUp?.quantity ? String(Number(props.fuelUp.quantity)) : '')
const price = ref(props.fuelUp?.total_price ?? '')
const lastOdometer = ref<number | null>(null)

const location = useGeolocation()
if (props.fuelUp?.latitude != null && props.fuelUp.longitude != null) {
  location.state.latitude = props.fuelUp.latitude
  location.state.longitude = props.fuelUp.longitude
  location.state.status = 'ok'
}

const fuel = computed(() => reference.fuel)
const fuelTypeOptions = computed(() => {
  const options = [...(fuel.value?.fuel_types ?? [])]
  // A fuel type read from a receipt may not be in the configured list
  if (fuelType.value && !options.includes(fuelType.value)) options.unshift(fuelType.value)
  return options
})

/** Whether the payment source can be chosen. A fuel-up can switch from waiting for a receipt to
 *  manual entry, but not back, and not once the values come from a receipt. */
const canChoosePayment = computed(() => {
  if (props.variant !== 'full') return false
  const f = props.fuelUp
  if (!f) return true
  return f.payment_source === 'email_receipt' && f.receipt_id === null
})
const showPaymentInputs = computed(
  () =>
    props.variant === 'full' &&
    (paymentSource.value === 'manual' || (props.fuelUp?.receipt_id ?? null) !== null),
)

onMounted(async () => {
  await loadReference()
  if (vehicleId.value === '') {
    const remembered = Number(localStorage.getItem(LAST_VEHICLE_KEY))
    const known = reference.vehicles.find((v) => v.id === remembered)
    vehicleId.value =
      known?.id ?? (reference.vehicles.length === 1 ? reference.vehicles[0]!.id : '')
  }
  if (props.autoLocate) location.locate()
})

watch(
  vehicleId,
  async (id) => {
    lastOdometer.value = null
    if (id === '') return
    try {
      lastOdometer.value = (
        await getJson<{ odometer: number | null }>(`/vehicles/${id}/odometer`)
      ).odometer
    } catch {
      // only a hint
    }
  },
  { immediate: true },
)

function submit() {
  if (vehicleId.value === '') return
  try {
    localStorage.setItem(LAST_VEHICLE_KEY, String(vehicleId.value))
  } catch {
    // storage may be blocked
  }
  const located = location.state.status === 'ok'
  const payload: Record<string, unknown> = {
    vehicle_id: vehicleId.value,
    odometer: Number(odometer.value),
    is_fill_to_full: fillToFull.value,
    missed_fuel_up: missed.value,
    latitude: located ? location.state.latitude : null,
    longitude: located ? location.state.longitude : null,
  }
  if (props.variant === 'full') {
    payload.fuel_up_time = parseLocalInput(time.value)?.toISOString()
    // When editing, the payment source is only sent if it changes
    if (!editing.value || paymentSource.value !== originalSource) {
      payload.payment_source = paymentSource.value
    }
    if (showPaymentInputs.value) {
      payload.fuel_type = fuelType.value || null
      payload.quantity = quantity.value === '' ? null : quantity.value
      payload.total_price = price.value === '' ? null : price.value
    }
  }
  emit('submit', payload)
}

// The browser blocks the form and shows this while the date and time isn't valid
const timeInput = ref<HTMLInputElement | null>(null)
function checkTime() {
  timeInput.value?.setCustomValidity(
    parseLocalInput(time.value) ? '' : 'Use the format YYYY-MM-DD HH:MM, e.g. 2026-10-05 17:08.',
  )
}
watch(time, checkTime)
onMounted(checkTime)

const locationUrl = computed(() => {
  const s = location.state
  return s.status === 'ok' && s.latitude !== null && s.longitude !== null
    ? coordinatesMapUrl(s.latitude, s.longitude)
    : null
})

const locationText = computed(() => {
  const s = location.state
  switch (s.status) {
    case 'locating':
      return 'Finding your location…'
    case 'ok':
      return `${s.latitude}, ${s.longitude}${s.accuracy ? ` (±${s.accuracy} m)` : ''}`
    case 'denied':
      return 'Location access was denied. Allow it in the browser settings to record where you fuelled.'
    case 'unavailable':
      return 'The location is not available right now.'
    default:
      return 'No location'
  }
})
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <div v-if="reference.vehiclesError" class="alert error" role="alert">
      {{ reference.vehiclesError }}
    </div>

    <div class="field">
      <label for="vehicle">Vehicle</label>
      <select id="vehicle" v-model="vehicleId" required>
        <option value="" disabled>Choose a vehicle</option>
        <option v-for="vehicle in reference.vehicles" :key="vehicle.id" :value="vehicle.id">
          {{ vehicleLabel(vehicle) }}
        </option>
      </select>
      <span v-if="!reference.vehicles.length && !reference.vehiclesError" class="hint">
        No vehicles available. Ask an admin to give you access to one.
      </span>
    </div>

    <div class="field">
      <label for="odometer">Odometer reading</label>
      <input
        id="odometer"
        v-model="odometer"
        type="number"
        inputmode="numeric"
        min="0"
        step="1"
        required
        autocomplete="off"
      />
      <span v-if="lastOdometer !== null" class="hint">
        Last reading in LubeLogger: {{ formatNumber(lastOdometer, 0) }}
      </span>
    </div>

    <div v-if="variant === 'full'" class="field">
      <label for="time">Date and time</label>
      <input
        id="time"
        ref="timeInput"
        v-model="time"
        type="text"
        placeholder="YYYY-MM-DD HH:MM"
        autocomplete="off"
        required
      />
    </div>

    <div class="field">
      <label class="check"><input v-model="fillToFull" type="checkbox" /> Full fuel-up</label>
      <label class="check">
        <input v-model="missed" type="checkbox" /> Missed fuel-up
        <span class="hint">(an earlier fuel-up was not logged)</span>
      </label>
    </div>

    <div class="field">
      <span class="label">Location</span>
      <a v-if="locationUrl" :href="locationUrl" target="_blank" rel="noopener">{{
        locationText
      }}</a>
      <span v-else :class="{ muted: location.state.status !== 'ok' }">{{ locationText }}</span>
      <div class="actions">
        <button
          type="button"
          :disabled="location.state.status === 'locating'"
          @click="location.locate()"
        >
          {{ location.state.status === 'ok' ? 'Update location' : 'Use my location' }}
        </button>
        <button
          v-if="location.state.status === 'ok'"
          class="link"
          type="button"
          @click="location.clear()"
        >
          Remove
        </button>
      </div>
    </div>

    <template v-if="variant === 'full'">
      <h2>Payment</h2>
      <div v-if="canChoosePayment" class="field">
        <label class="check">
          <input v-model="paymentSource" type="radio" value="email_receipt" />
          Pace Drive email receipt
        </label>
        <label class="check">
          <input v-model="paymentSource" type="radio" value="manual" /> Enter it manually
        </label>
      </div>

      <p v-if="paymentSource === 'email_receipt' && !showPaymentInputs" class="muted">
        The fuel type, amount, price and station address are read from the receipt that Pace Drive
        emails you. It is matched to this fuel-up by its time.
      </p>

      <template v-if="showPaymentInputs">
        <div class="field">
          <label for="fuel-type">Fuel type</label>
          <select id="fuel-type" v-model="fuelType" required>
            <option value="" disabled>Choose a fuel type</option>
            <option v-for="type in fuelTypeOptions" :key="type" :value="type">{{ type }}</option>
          </select>
        </div>
        <div class="row">
          <div class="field">
            <label for="quantity">Fuel amount</label>
            <div class="input-suffix">
              <input
                id="quantity"
                v-model="quantity"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                required
              />
              <span>{{ fuel?.volume_unit }}</span>
            </div>
          </div>
          <div class="field">
            <label for="price">Total price</label>
            <div class="input-suffix">
              <input
                id="price"
                v-model="price"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                required
              />
              <span>{{ fuel?.currency }}</span>
            </div>
          </div>
        </div>
      </template>
    </template>

    <button class="primary" type="submit" :disabled="busy || vehicleId === ''">
      {{ submitLabel }}
    </button>
  </form>
</template>
