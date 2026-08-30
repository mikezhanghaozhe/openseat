import type {
  ClaimSeatResponse,
  ErrorBody,
  Observation,
  RoomSummary,
  WsTicketResponse,
} from './protocol'

const API_URL = import.meta.env.VITE_API_URL as string

export class ApiError extends Error {
  body: ErrorBody
  status: number
  constructor(status: number, body: ErrorBody) {
    super(body.reason || body.error)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  const body = await res.json()
  if (!res.ok) throw new ApiError(res.status, body as ErrorBody)
  return body as T
}

export function getRoom(roomId: string): Promise<RoomSummary> {
  return request(`/v1/rooms/${roomId}`)
}

export function claimSeat(
  roomId: string,
  args: { invite_token: string; seat?: number; kind: 'human'; display_name: string },
): Promise<ClaimSeatResponse> {
  return request(`/v1/rooms/${roomId}/seats`, {
    method: 'POST',
    body: JSON.stringify(args),
  })
}

export function getView(roomId: string, seatToken: string): Promise<Observation> {
  return request(`/v1/rooms/${roomId}/view`, {
    headers: { Authorization: `Bearer ${seatToken}` },
  })
}

export function getWsTicket(roomId: string, seatToken: string): Promise<WsTicketResponse> {
  return request(`/v1/rooms/${roomId}/ws-ticket`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${seatToken}` },
  })
}

export function wsUrl(roomId: string, ticket: string): string {
  const base = API_URL.replace(/^http/, 'ws')
  return `${base}/v1/rooms/${roomId}/ws?ticket=${encodeURIComponent(ticket)}`
}

// --- seat_token storage, keyed by room_id (per SCREENS §1) ---

export function storeSeatToken(roomId: string, seatToken: string) {
  localStorage.setItem(`openseat:seat_token:${roomId}`, seatToken)
}

export function loadSeatToken(roomId: string): string | null {
  return localStorage.getItem(`openseat:seat_token:${roomId}`)
}
