// Mirrors docs/PROTOCOL.md §4 (Observation) and related payload shapes.
// Do not add fields here that the protocol doesn't send — absence is meaningful (redaction).

export type Card = string; // e.g. "Ah", "Td", or "??" for a known-hidden card

export type SeatStatus = 'active' | 'folded' | 'all_in';

export type SeatKind = 'human' | 'model' | 'agent';

export type Phase =
  | 'waiting'
  | 'preflop'
  | 'flop'
  | 'turn'
  | 'river'
  | 'showdown'
  | 'hand_complete';

export type ActionType = 'fold' | 'check' | 'call' | 'raise' | 'show' | 'muck';

export interface Action {
  type: ActionType;
  to?: number; // raise only, total for the street
}

export interface LegalAction {
  type: ActionType;
  amount?: number; // call
  min_to?: number; // raise
  max_to?: number; // raise
}

export interface SeatPublic {
  seat: number;
  name: string;
  kind: SeatKind;
  stack: number;
  committed_street: number;
  status: SeatStatus;
  last_action: Action | null;
  hole?: Card[]; // absent unless this is `you`, or after showdown reveal
  revealed?: Card[]; // present after a showdown reveal
}

export interface You {
  seat: number;
  name: string;
  hole: Card[];
  stack: number;
  committed_street: number;
  committed_hand: number;
  status: SeatStatus;
}

export interface Pot {
  index: number;
  amount: number;
  eligible_seats: number[];
}

export interface ChatMessage {
  seq: number;
  seat: number;
  name: string;
  text: string;
}

export interface Observation {
  protocol_version: string;
  seq: number;
  room_id: string;
  hand_no: number;
  phase: Phase;
  to_act: number | null;
  button: number;

  you: You;

  board: Card[];
  pots: Pot[];
  pot_total: number;

  seats: SeatPublic[];

  to_call: number;
  min_raise_to: number | null;
  max_raise_to: number | null;
  legal_actions: LegalAction[];

  chat: ChatMessage[];

  text: string;
}

// --- Events (docs/PROTOCOL.md §5) ---
// Wire shape is `{ seq, type, ts, payload: {...} }` — `payload` is a real nested
// field (packages/engine/types.py Event.payload), not a spread of its keys.

export interface RoomEvent {
  seq: number;
  type: string;
  ts: number;
  payload: Record<string, unknown>;
}

export interface EventsResponse {
  events: RoomEvent[];
  latest_seq: number;
}

// --- Rooms / seats (docs/PROTOCOL.md §6) ---

export interface OpenSeatSummary {
  index: number;
  status: 'open' | 'claimed';
  name?: string;
  kind?: SeatKind;
}

export type RoomLifecycle = 'waiting' | 'in_progress' | 'complete';

export interface RoomSummary {
  room_id: string;
  protocol_version: string;
  game: string;
  phase: Phase;
  status: RoomLifecycle;
  hand_no: number;
  seats: OpenSeatSummary[];
}

export interface CreateRoomResponse {
  room_id: string;
  invite_token: string;
  host_token: string;
  seats: { index: number; status: 'open' | 'claimed' }[];
}

export interface ClaimSeatResponse {
  seat_token: string;
  seat_index: number;
}

export interface ActionResponse {
  first_seq: number;
  last_seq: number;
  accepted: boolean;
  replayed?: boolean;
}

export interface PotAward {
  index: number;
  amount: number;
  awards: { seat: number; amount: number }[];
  reason: 'uncontested' | 'showdown';
}

export interface Reveal {
  seat: number;
  hole: Card[];
  rank_class: string;
  description: string;
}

export interface ResultResponse {
  hand_no: number;
  pots: PotAward[];
  final_stacks: number[];
  showdown: Reveal[];
}

export interface ApiErrorBody {
  error: string;
  reason: string;
  legal_actions?: LegalAction[];
  [key: string]: unknown;
}
