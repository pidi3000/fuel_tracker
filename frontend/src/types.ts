export type Role = 'admin' | 'user'

export interface User {
  id: number
  username: string
  email: string | null
  role: Role
  is_active: boolean
  vehicle_ids: number[]
  created_at: string
}

export interface ApiToken {
  id: number
  name: string
  created_at: string
  last_used_at: string | null
}

export interface ApiTokenCreated extends ApiToken {
  token: string
}

export interface Vehicle {
  id: number
  name: string
  identifier: string
}

export interface FuelTypes {
  fuel_types: string[]
  volume_unit: string
  currency: string
  tz: string
  review_before_send: boolean
}

export type FuelUpStatus = 'pending' | 'needs_attention' | 'done' | 'failed'

export interface FuelUp {
  id: number
  vehicle_id: number
  vehicle_name: string
  odometer: number
  fuel_up_time: string
  is_fill_to_full: boolean
  missed_fuel_up: boolean
  latitude: number | null
  longitude: number | null
  payment_source: 'manual' | 'email_receipt'
  fuel_type: string | null
  quantity: string | null
  total_price: string | null
  volume_unit: string
  currency: string
  address: string | null
  status: FuelUpStatus
  sending: boolean
  attention: string | null
  attention_message: string | null
  warnings: string[]
  error_message: string | null
  lubelogger_record_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  editable: boolean
  receipt_id: number | null
  waiting_for_receipt: boolean
  receipt_deadline: string | null
}

export interface Receipt {
  id: number
  state: 'unmatched' | 'matched' | 'ignored'
  mail_subject: string
  station: string | null
  address: string | null
  paid_at: string | null
  printed_date: string | null
  fuel_type: string | null
  quantity: string | null
  unit: string | null
  total: string | null
  currency: string | null
  transaction_id: string | null
  missing: string[]
  warnings: string[]
  parse_error: string | null
  created_at: string
  fuel_up_id: number | null
  has_pdf: boolean
}

export interface HistoryRecord {
  id: number
  vehicle_id: number
  vehicle_name: string
  date: string
  odometer: number
  fuel_consumed: string
  cost: string
  is_fill_to_full: boolean
  missed_fuel_up: boolean
  notes: string
  gps: string | null
  address: string | null
  files: { name: string; location: string }[]
}

export interface AppNotification {
  id: number
  level: 'info' | 'warning' | 'error'
  title: string
  message: string
  fuel_up_id: number | null
  is_read: boolean
  created_at: string
}
