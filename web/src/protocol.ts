// Types mirror docs/PROTOCOL.md §4 (Observation), §5 (Events), §8 (WebSocket). Do not add
// fields not defined there — see AGENTS.md ownership map, docs/PROTOCOL.md is not ours to edit.

export type SeatKind = 'human' | 'model' | 'agent'
export type SeatStatus = 'active' | 'folded' | 'all_in'
export type Phase =
  | 'waiting'
  | 'preflop'
  | 'flop'
  | 'turn'
  | 'river'
  | 'showdown'
  | 'hand_complete'

export interface ActionTaken {
  type: 'fold' | 'check' | 'call' | 'raise' | 'show' | 'muck'
  to?: number
}

export interface Reveal {
  seat: number
  hole: string[]
  rank_class: string
  description: string
}

export interface SeatView {
  seat: number
  name: string
  kind: SeatKind
  stack: number
  committed_street: number
  status: SeatStatus
  last_action: ActionTaken | null
  revealed?: Reveal // present only after this seat's showdown reveal
}

export interface You {
  seat: number
  name: string
  hole: string[]
  stack: number
  committed_street: number
  committed_hand?: number // omitted when phase === 'hand_complete'
  status: SeatStatus
}

export interface Pot {
  index: number
  amount: number
  eligible_seats: number[]
}

export type LegalAction =
  | { type: 'fold' }
  | { type: 'check' }
  | { type: 'call'; amount: number }
  | { type: 'raise'; min_to: number; max_to: number }
  | { type: 'show' }
  | { type: 'muck' }

export interface ChatMessage {
  seq: number
  seat: number
  name: string
  text: string
}

export interface Observation {
  protocol_version: string
  seq: number
  room_id: string
  hand_no: number
  phase: Phase
  to_act: number | null
  button: number
  you: You
  board: string[]
  pots: Pot[]
  pot_total: number
  seats: SeatView[]
  to_call?: number
  min_raise_to?: number
  max_raise_to?: number
  legal_actions: LegalAction[]
  chat: ChatMessage[]
  text: string
}

export interface EventBase {
  seq: number
  type: string
  ts: number
  [key: string]: unknown
}

// The room server's generic dataclass serializer (`to_wire`) does not spread
// `Event.payload` into the envelope despite §5's `{ seq, type, ts, ...payload }`
// notation — it nests it under a literal `payload` key instead. Every event we
// receive (hello.replay, event frames, resume replay) arrives in this raw shape;
// normalizeEvent() flattens it once at the socket boundary so the rest of the
// app can treat events as flat per §5.
export interface RawWireEvent {
  seq: number
  type: string
  ts: number
  payload: Record<string, unknown>
}

export function normalizeEvent(raw: RawWireEvent): EventBase {
  return { ...raw.payload, seq: raw.seq, type: raw.type, ts: raw.ts }
}

export interface PotAward {
  index: number
  amount: number
  awards: { seat: number; amount: number }[]
  reason: 'uncontested' | 'showdown'
}

// --- WebSocket frames, §8 ---

export type ServerFrame =
  | { t: 'hello'; seq: number; seat: number | null; replay: RawWireEvent[] }
  | { t: 'state'; payload: Observation }
  | { t: 'event'; payload: RawWireEvent }
  | { t: 'clock'; seat: number; deadline_ms: number }
  | { t: 'error'; code: string; reason: string; legal_actions?: LegalAction[] }
  | { t: 'pong' }

export type ClientFrame =
  | { t: 'act'; request_id: string; action: Action; table_talk?: string }
  | { t: 'resume'; since: number }
  | { t: 'ping' }

export type Action =
  | { type: 'fold' }
  | { type: 'check' }
  | { type: 'call' }
  | { type: 'raise'; to: number }
  | { type: 'show' }
  | { type: 'muck' }

// --- REST shapes, §6 ---

export interface RoomSeatSummary {
  index: number
  status: 'open' | 'claimed'
  name?: string
  kind?: SeatKind
}

// Room.summary() (packages/room_server/store.py) — no `config` field exists here;
// blinds/stacks are not derivable from this endpoint.
export interface RoomSummary {
  protocol_version: string
  room_id: string
  game: string
  phase: Phase
  seats: RoomSeatSummary[]
  hand_no: number
  status: 'waiting' | 'in_progress' | 'complete'
}

export interface ClaimSeatResponse {
  protocol_version: string
  seat_token: string
  seat_index: number
}

export interface WsTicketResponse {
  protocol_version: string
  ticket: string
  expires_in: number
}

export interface ErrorBody {
  error: string
  reason: string
  legal_actions?: LegalAction[]
  [key: string]: unknown
}
