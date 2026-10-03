<script setup lang="ts">
import { dismissToast, toasts } from '../toast'
</script>

<template>
  <div class="toasts" aria-live="polite">
    <div v-for="toast in toasts" :key="toast.id" class="toast alert" :class="toast.kind">
      <span>{{ toast.text }}</span>
      <button class="link" type="button" aria-label="Dismiss" @click="dismissToast(toast.id)">
        ×
      </button>
    </div>
  </div>
</template>
