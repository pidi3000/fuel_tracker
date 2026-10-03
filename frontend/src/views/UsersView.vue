<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { deleteJson, getJson, patchJson, postJson } from '../api'
import { auth } from '../auth'
import { loadReference, reference, vehicleLabel } from '../reference'
import { showToast } from '../toast'
import type { Role, User } from '../types'

const users = ref<User[]>([])
const error = ref('')
const creating = ref(false)
const editingId = ref<number | null>(null)

const emptyForm = () => ({
  username: '',
  email: '',
  password: '',
  role: 'user' as Role,
  is_active: true,
  vehicle_ids: [] as number[],
})
const form = reactive(emptyForm())

async function load() {
  try {
    users.value = await getJson<User[]>('/users')
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(async () => {
  await Promise.all([load(), loadReference(true)])
})

function startCreate() {
  Object.assign(form, emptyForm())
  editingId.value = null
  creating.value = true
  error.value = ''
}

function startEdit(user: User) {
  Object.assign(form, {
    username: user.username,
    email: user.email ?? '',
    password: '',
    role: user.role,
    is_active: user.is_active,
    vehicle_ids: [...user.vehicle_ids],
  })
  creating.value = false
  editingId.value = user.id
  error.value = ''
}

function cancel() {
  creating.value = false
  editingId.value = null
}

const editingSelf = computed(() => editingId.value === auth.user?.id)

async function save() {
  error.value = ''
  try {
    if (creating.value) {
      await postJson('/users', {
        username: form.username,
        email: form.email || null,
        password: form.password,
        role: form.role,
        vehicle_ids: form.vehicle_ids,
      })
      showToast(`User ${form.username} created.`)
    } else if (editingId.value !== null) {
      await patchJson(`/users/${editingId.value}`, {
        email: form.email || null,
        role: form.role,
        is_active: form.is_active,
        vehicle_ids: form.vehicle_ids,
        ...(form.password ? { password: form.password } : {}),
      })
      showToast('User saved.')
    }
    cancel()
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function remove(user: User) {
  if (!confirm(`Delete the user ${user.username}? Their fuel-ups stay.`)) return
  try {
    await deleteJson(`/users/${user.id}`)
    showToast('User deleted.', 'info')
    cancel()
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

function vehicleSummary(user: User): string {
  if (user.role === 'admin') return 'all'
  return user.vehicle_ids.length ? String(user.vehicle_ids.length) : 'none'
}
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>Users</h1>
      <button class="primary" type="button" @click="startCreate">Add user</button>
    </div>

    <div v-if="error" class="alert error" role="alert">{{ error }}</div>

    <form v-if="creating || editingId !== null" class="card" @submit.prevent="save">
      <h2>{{ creating ? 'New user' : `Edit ${form.username}` }}</h2>
      <div v-if="creating" class="field">
        <label for="u-name">Username</label>
        <input id="u-name" v-model="form.username" required minlength="3" autocomplete="off" />
      </div>
      <div class="field">
        <label for="u-email">Email (optional)</label>
        <input id="u-email" v-model="form.email" type="email" autocomplete="off" />
      </div>
      <div class="field">
        <label for="u-password">{{ creating ? 'Password' : 'New password' }}</label>
        <input
          id="u-password"
          v-model="form.password"
          type="password"
          :required="creating"
          minlength="8"
          autocomplete="new-password"
        />
        <span v-if="!creating" class="hint"
          >Leave empty to keep the password. Changing it signs the user out.</span
        >
      </div>
      <div class="field">
        <label for="u-role">Role</label>
        <select id="u-role" v-model="form.role" :disabled="editingSelf">
          <option value="user">User: logs fuel-ups for assigned vehicles</option>
          <option value="admin">Admin: manages users and settings, all vehicles</option>
        </select>
      </div>
      <div v-if="!creating" class="field">
        <label class="check">
          <input v-model="form.is_active" type="checkbox" :disabled="editingSelf" /> Can sign in
        </label>
      </div>
      <div class="field">
        <span class="label">Vehicles</span>
        <span v-if="form.role === 'admin'" class="hint">Admins can use all vehicles.</span>
        <template v-else>
          <label v-for="vehicle in reference.vehicles" :key="vehicle.id" class="check">
            <input v-model="form.vehicle_ids" type="checkbox" :value="vehicle.id" />
            {{ vehicleLabel(vehicle) }}
          </label>
          <span v-if="!reference.vehicles.length" class="hint">
            {{ reference.vehiclesError || 'No vehicles found in LubeLogger.' }}
          </span>
        </template>
      </div>
      <div class="actions">
        <button class="primary" type="submit">{{ creating ? 'Create user' : 'Save' }}</button>
        <button type="button" @click="cancel">Cancel</button>
        <button
          v-if="!creating && !editingSelf"
          class="danger"
          type="button"
          style="margin-left: auto"
          @click="remove(users.find((u) => u.id === editingId)!)"
        >
          Delete user
        </button>
      </div>
    </form>

    <section class="card">
      <table>
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Vehicles</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id">
            <td>
              {{ user.username }}
              <span v-if="user.id === auth.user?.id" class="muted small">(you)</span>
              <div v-if="user.email" class="muted small">{{ user.email }}</div>
            </td>
            <td>
              <span class="badge" :class="user.is_active ? '' : 'error'">
                {{ user.is_active ? user.role : 'disabled' }}
              </span>
            </td>
            <td>{{ vehicleSummary(user) }}</td>
            <td><button type="button" @click="startEdit(user)">Edit</button></td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>
