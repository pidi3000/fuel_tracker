<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { deleteJson, getJson, putJson } from '../api'
import { showToast } from '../toast'
import type {
  ConnectionStatus,
  EnvironmentInfo,
  Setting,
  SettingsResponse,
  StatusResponse,
} from '../types'

const settings = ref<Setting[]>([])
const environment = ref<EnvironmentInfo | null>(null)
const status = ref<StatusResponse | null>(null)
const checking = ref(false)
const error = ref('')
// What is typed in each field, as text (lists as "A, B", booleans as true/false)
const drafts = reactive<Record<string, string | boolean>>({})
const fieldErrors = reactive<Record<string, string>>({})

function toDraft(setting: Setting): string | boolean {
  if (setting.kind === 'bool') return setting.value as boolean
  if (setting.kind === 'list') return (setting.value as string[]).join(', ')
  return String(setting.value)
}

function fromDraft(setting: Setting): unknown {
  const draft = drafts[setting.key]!
  if (setting.kind === 'int') return draft === '' ? NaN : Number(draft)
  return draft
}

function applyResponse(response: SettingsResponse) {
  settings.value = response.settings
  environment.value = response.environment
  for (const setting of response.settings) drafts[setting.key] = toDraft(setting)
}

async function load() {
  try {
    applyResponse(await getJson<SettingsResponse>('/settings'))
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function check() {
  checking.value = true
  try {
    status.value = await getJson<StatusResponse>('/status')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    checking.value = false
  }
}

function replace(updated: Setting) {
  const index = settings.value.findIndex((s) => s.key === updated.key)
  settings.value[index] = updated
  drafts[updated.key] = toDraft(updated)
  fieldErrors[updated.key] = ''
}

function changed(setting: Setting): boolean {
  return JSON.stringify(drafts[setting.key]) !== JSON.stringify(toDraft(setting))
}

async function save(setting: Setting) {
  fieldErrors[setting.key] = ''
  try {
    replace(await putJson<Setting>(`/settings/${setting.key}`, { value: fromDraft(setting) }))
    showToast(`${setting.label} saved.`)
  } catch (e) {
    fieldErrors[setting.key] = (e as Error).message
  }
}

async function reset(setting: Setting) {
  try {
    replace(await deleteJson<Setting>(`/settings/${setting.key}`))
    showToast(`${setting.label} is back to its default.`, 'info')
  } catch (e) {
    fieldErrors[setting.key] = (e as Error).message
  }
}

function format(value: Setting['value']): string {
  return Array.isArray(value) ? value.join(', ') : String(value)
}

const stateLabel: Record<ConnectionStatus['state'], string> = {
  ok: 'Working',
  error: 'Problem',
  not_configured: 'Not set up',
  connecting: 'Connecting…',
}
const stateKind: Record<ConnectionStatus['state'], string> = {
  ok: 'ok',
  error: 'error',
  not_configured: '',
  connecting: 'warn',
}

onMounted(() => {
  void load()
  void check()
})
</script>

<template>
  <main class="page">
    <h1>Settings</h1>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

    <section class="card">
      <div class="item-head">
        <h2>Connections</h2>
        <button type="button" :disabled="checking" @click="check">Check again</button>
      </div>
      <table v-if="status" class="kv">
        <tbody>
          <tr
            v-for="(name, key) in { lubelogger: 'LubeLogger', mailbox: 'Receipt mailbox' }"
            :key="key"
          >
            <th>{{ name }}</th>
            <td>
              <span class="badge" :class="stateKind[status[key].state]">{{
                stateLabel[status[key].state]
              }}</span>
              <div v-if="status[key].message" class="small muted">{{ status[key].message }}</div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">Checking…</p>
    </section>

    <section class="card">
      <h2>Settings</h2>
      <p class="muted small">
        These start from the environment variables. A value saved here wins over them until you
        reset it.
      </p>
      <div v-for="setting in settings" :key="setting.key" class="setting">
        <div class="item-head">
          <label :for="`setting-${setting.key}`" class="setting-label">{{ setting.label }}</label>
          <span v-if="setting.overridden" class="badge">changed</span>
        </div>
        <div class="small muted">{{ setting.description }}</div>
        <div class="setting-input">
          <label v-if="setting.kind === 'bool'" class="check">
            <input :id="`setting-${setting.key}`" v-model="drafts[setting.key]" type="checkbox" />
            {{ drafts[setting.key] ? 'On' : 'Off' }}
          </label>
          <input
            v-else-if="setting.kind === 'int'"
            :id="`setting-${setting.key}`"
            v-model="drafts[setting.key]"
            type="number"
            :min="setting.minimum ?? undefined"
            :max="setting.maximum ?? undefined"
            step="1"
          />
          <input v-else :id="`setting-${setting.key}`" v-model="drafts[setting.key]" type="text" />
          <button
            type="button"
            class="primary"
            :disabled="!changed(setting)"
            @click="save(setting)"
          >
            Save
          </button>
          <button v-if="setting.overridden" type="button" @click="reset(setting)">Reset</button>
        </div>
        <div class="small muted">Default: {{ format(setting.default) }}</div>
        <div v-if="fieldErrors[setting.key]" class="small error-text">
          {{ fieldErrors[setting.key] }}
        </div>
      </div>
    </section>

    <section v-if="environment" class="card">
      <h2>Connection details</h2>
      <p class="muted small">
        Set with environment variables (see <code>.env.example</code>); passwords are never shown.
      </p>
      <table class="kv">
        <tbody>
          <tr>
            <th>Version</th>
            <td>{{ environment.version }}</td>
          </tr>
          <tr>
            <th>LubeLogger</th>
            <td>
              {{ environment.lubelogger_url || 'not set' }}
              <span class="muted small">
                (API key {{ environment.lubelogger_api_key_set ? 'set' : 'not set' }})
              </span>
            </td>
          </tr>
          <tr>
            <th>Extra fields</th>
            <td>
              {{ environment.lubelogger_field_gps }}, {{ environment.lubelogger_field_address }}
            </td>
          </tr>
          <tr>
            <th>Mailbox</th>
            <td>
              <template v-if="environment.imap_host">
                {{ environment.imap_user }} @ {{ environment.imap_host }}:{{
                  environment.imap_port
                }}
              </template>
              <template v-else>not set</template>
            </td>
          </tr>
          <tr>
            <th>Folders</th>
            <td>{{ environment.imap_inbox }} → {{ environment.imap_processed_folder }}</td>
          </tr>
          <tr>
            <th>Receipt emails</th>
            <td class="small">
              from {{ environment.receipt_sender }}, subject matching
              <code>{{ environment.receipt_subject_pattern }}</code>
            </td>
          </tr>
          <tr>
            <th>Login lasts</th>
            <td>{{ environment.session_days }} days</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>
