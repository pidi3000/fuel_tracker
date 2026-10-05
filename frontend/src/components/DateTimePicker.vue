<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { isoDate, pad, parseLocalInput, toLocalInput } from '../format'

/**
 * Date and time input with a calendar. The value is the text "2026-09-23 17:08" (local time, ISO
 * order, 24 hours), so it can be typed or picked. The browser's own date picker is not used,
 * because it follows the language of the browser instead of ISO.
 */
const model = defineModel<string>({ required: true })
withDefaults(defineProps<{ id?: string; required?: boolean }>(), { id: undefined })

const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'] // ISO weeks start on Monday
const HOURS = Array.from({ length: 24 }, (_, i) => i)
const MINUTES = Array.from({ length: 60 }, (_, i) => i)

const root = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)
const toggleButton = ref<HTMLButtonElement | null>(null)
const days = ref<HTMLElement | null>(null)

const open = ref(false)
const view = reactive({ year: 0, month: 0 }) // the month shown; month is 0-11
const focusDay = ref(new Date()) // the day that has the keyboard focus
const now = ref(new Date()) // set when the picker opens

const selected = computed(() => parseLocalInput(model.value))
/** What the time inputs show: the value, or now while the text isn't a valid date and time. */
const base = computed(() => selected.value ?? now.value)

const title = computed(() => `${view.year}-${pad(view.month + 1)}`)

const cells = computed(() => {
  const first = new Date(view.year, view.month, 1)
  const offset = (first.getDay() + 6) % 7 // days of the previous month shown before the 1st
  const selectedKey = selected.value ? isoDate(selected.value) : null
  const todayKey = isoDate(now.value)
  // Always six weeks, so the calendar doesn't change height from month to month
  return Array.from({ length: 42 }, (_, i) => {
    const date = new Date(view.year, view.month, 1 - offset + i)
    const key = isoDate(date)
    return {
      date,
      key,
      outside: date.getMonth() !== view.month,
      selected: key === selectedKey,
      today: key === todayKey,
    }
  })
})

// Only one day is reachable with Tab; the arrow keys move between the days
const tabbableKey = computed(() => {
  const f = focusDay.value
  const inView = f.getFullYear() === view.year && f.getMonth() === view.month
  return isoDate(inView ? f : new Date(view.year, view.month, 1))
})

function addDays(date: Date, n: number): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + n)
}

/** Moves by months and stays in that month: 31 January plus one month is 28 February. */
function addMonths(date: Date, n: number): Date {
  const target = new Date(date.getFullYear(), date.getMonth() + n, 1)
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  target.setDate(Math.min(date.getDate(), lastDay))
  return target
}

function showMonth(date: Date) {
  view.year = date.getFullYear()
  view.month = date.getMonth()
}

function focusDayButton(date: Date) {
  void nextTick(() => {
    days.value?.querySelector<HTMLElement>(`[data-day="${isoDate(date)}"]`)?.focus()
  })
}

function openPicker() {
  now.value = new Date()
  const start = selected.value ?? now.value
  focusDay.value = start
  showMonth(start)
  open.value = true
  focusDayButton(start)
}

function closePicker(returnFocus = false) {
  open.value = false
  if (returnFocus) toggleButton.value?.focus()
}

function shiftView(months: number) {
  showMonth(addMonths(new Date(view.year, view.month, 1), months))
}

/** Writes the value from a day and/or an hour and minute. What is left out stays as it is (or is
 *  taken from now, while the text isn't a valid date and time). */
function setValue(date: Date | null, hours?: number, minutes?: number) {
  const day = date ?? base.value
  const time = base.value
  model.value = toLocalInput(
    new Date(
      day.getFullYear(),
      day.getMonth(),
      day.getDate(),
      hours ?? time.getHours(),
      minutes ?? time.getMinutes(),
    ),
  )
}

function setNow() {
  now.value = new Date()
  model.value = toLocalInput(now.value)
}

function onHour(event: Event) {
  setValue(null, Number((event.target as HTMLSelectElement).value))
}

function onMinute(event: Event) {
  setValue(null, undefined, Number((event.target as HTMLSelectElement).value))
}

function onDaysKey(event: KeyboardEvent) {
  const from = focusDay.value
  const next =
    event.key === 'ArrowLeft'
      ? addDays(from, -1)
      : event.key === 'ArrowRight'
        ? addDays(from, 1)
        : event.key === 'ArrowUp'
          ? addDays(from, -7)
          : event.key === 'ArrowDown'
            ? addDays(from, 7)
            : event.key === 'PageUp'
              ? addMonths(from, -1)
              : event.key === 'PageDown'
                ? addMonths(from, 1)
                : null
  if (!next) return
  event.preventDefault()
  focusDay.value = next
  showMonth(next)
  focusDayButton(next)
}

function onEscape(event: KeyboardEvent) {
  if (!open.value) return
  event.stopPropagation()
  closePicker(true)
}

// Close when the focus moves to something else, or when something else is clicked
function onFocusOut(event: FocusEvent) {
  const next = event.relatedTarget as Node | null
  if (next && !root.value?.contains(next)) open.value = false
}

function onPointerDown(event: PointerEvent) {
  if (open.value && !root.value?.contains(event.target as Node)) open.value = false
}

onMounted(() => document.addEventListener('pointerdown', onPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onPointerDown))

// When another date is typed or picked, show its month (the time doesn't move the calendar)
watch(
  () => model.value.slice(0, 10),
  () => {
    if (selected.value) showMonth(selected.value)
  },
)

// The browser blocks the form and shows this while the text isn't a valid date and time
function validate() {
  input.value?.setCustomValidity(
    selected.value ? '' : 'Use the format YYYY-MM-DD HH:MM, e.g. 2026-10-05 17:08.',
  )
}
watch(model, validate)
onMounted(validate)
</script>

<template>
  <div ref="root" class="datetime" @keydown.esc="onEscape" @focusout="onFocusOut">
    <div class="datetime-control">
      <input
        :id="id"
        ref="input"
        v-model="model"
        type="text"
        placeholder="YYYY-MM-DD HH:MM"
        autocomplete="off"
        :required="required"
      />
      <button
        ref="toggleButton"
        class="datetime-toggle"
        type="button"
        aria-label="Choose date and time"
        aria-haspopup="dialog"
        :aria-expanded="open"
        @click="open ? closePicker() : openPicker()"
      >
        <svg
          viewBox="0 0 24 24"
          width="20"
          height="20"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <path d="M16 2v4M8 2v4M3 10h18" />
        </svg>
      </button>
    </div>

    <div v-if="open" class="datetime-popover" role="dialog" aria-label="Choose date and time">
      <div class="datetime-nav">
        <button type="button" aria-label="Previous year" @click="shiftView(-12)">«</button>
        <button type="button" aria-label="Previous month" @click="shiftView(-1)">‹</button>
        <span class="datetime-title" aria-live="polite">{{ title }}</span>
        <button type="button" aria-label="Next month" @click="shiftView(1)">›</button>
        <button type="button" aria-label="Next year" @click="shiftView(12)">»</button>
      </div>

      <div ref="days" class="datetime-days" @keydown="onDaysKey">
        <span v-for="name in WEEKDAYS" :key="name" class="datetime-weekday" aria-hidden="true">
          {{ name }}
        </span>
        <button
          v-for="cell in cells"
          :key="cell.key"
          class="datetime-day"
          :class="{ outside: cell.outside, selected: cell.selected, today: cell.today }"
          type="button"
          :data-day="cell.key"
          :tabindex="cell.key === tabbableKey ? 0 : -1"
          :aria-label="cell.key"
          :aria-pressed="cell.selected"
          :aria-current="cell.today ? 'date' : undefined"
          @focus="focusDay = cell.date"
          @click="setValue(cell.date)"
        >
          {{ cell.date.getDate() }}
        </button>
      </div>

      <div class="datetime-footer">
        <button type="button" @click="setNow">Now</button>
        <span class="datetime-time">
          <select aria-label="Hour" :value="base.getHours()" @change="onHour">
            <option v-for="hour in HOURS" :key="hour" :value="hour">{{ pad(hour) }}</option>
          </select>
          :
          <select aria-label="Minute" :value="base.getMinutes()" @change="onMinute">
            <option v-for="minute in MINUTES" :key="minute" :value="minute">
              {{ pad(minute) }}
            </option>
          </select>
        </span>
        <button class="primary" type="button" @click="closePicker(true)">Done</button>
      </div>
    </div>
  </div>
</template>
