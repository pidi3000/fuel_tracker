import { reactive } from 'vue'

import { getJson } from './api'
import type { FuelTypes, Vehicle } from './types'

/** Vehicles and fuel types, loaded once per page load and reloaded on demand. */
export const reference = reactive({
  vehicles: [] as Vehicle[],
  fuel: null as FuelTypes | null,
  vehiclesError: '',
})

export async function loadReference(force = false): Promise<void> {
  if (!force && reference.fuel) return
  reference.fuel = await getJson<FuelTypes>('/fuel-types')
  try {
    reference.vehicles = await getJson<Vehicle[]>('/vehicles')
    reference.vehiclesError = ''
  } catch (e) {
    reference.vehicles = []
    reference.vehiclesError = (e as Error).message
  }
}

export function vehicleLabel(vehicle: Vehicle): string {
  return vehicle.identifier ? `${vehicle.name} (${vehicle.identifier})` : vehicle.name
}
