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
