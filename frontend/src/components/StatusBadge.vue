<script setup lang="ts">
import { computed } from 'vue'

import type { FuelUp } from '../types'

const props = defineProps<{ fuelUp: Pick<FuelUp, 'status' | 'sending' | 'attention'> }>()

const label = computed(() => {
  const { status, sending } = props.fuelUp
  if (status === 'pending') return sending ? 'Sending' : 'Pending'
  if (status === 'needs_attention') return 'Needs attention'
  if (status === 'done') return 'Done'
  return 'Failed'
})

const kind = computed(() => {
  switch (props.fuelUp.status) {
    case 'done':
      return 'ok'
    case 'needs_attention':
      return 'warn'
    case 'failed':
      return 'error'
    default:
      return ''
  }
})
</script>

<template>
  <span class="badge" :class="kind">{{ label }}</span>
</template>
