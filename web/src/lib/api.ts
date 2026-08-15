import type {
  Action,
  ActionResponse,
  ApiErrorBody,
  ClaimSeatResponse,
  CreateRoomResponse,
  EventsResponse,
  Observation,
  ResultResponse,
  RoomSummary,
  SeatKind,
} from './types';

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.reason ?? body.error);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({
      error: 'unknown',
      reason: res.statusText,
    }))) as ApiErrorBody;
    throw new ApiError(res.status, body);
  }
  return res.json() as Promise<T>;
}

export interface CreateRoomConfig {
  seats: number;
  sb: number;
  bb: number;
  starting_stack: number;
  turn_seconds: number;
}

export function createRoom(config: CreateRoomConfig): Promise<CreateRoomResponse> {
  return request('/v1/rooms', {
    method: 'POST',
    body: JSON.stringify({
      game: 'holdem-nl',
      seats: config.seats,
      config: {
        sb: config.sb,
        bb: config.bb,
        starting_stack: config.starting_stack,
        turn_seconds: config.turn_seconds,
      },
    }),
  });
}

export function getRoom(roomId: string): Promise<RoomSummary> {
  return request(`/v1/rooms/${roomId}`);
}

export function startRoom(
  roomId: string,
  hostToken: string
): Promise<{ hand_no: number; to_act: number; first_seq: number; last_seq: number }> {
  return request(`/v1/rooms/${roomId}/start`, {
    method: 'POST',
    body: JSON.stringify({ host_token: hostToken }),
  });
}

export function getResult(roomId: string): Promise<ResultResponse> {
  return request(`/v1/rooms/${roomId}/result`);
}

export function claimSeat(
  roomId: string,
  inviteToken: string,
  displayName: string,
  seat?: number,
  kind: SeatKind = 'human'
): Promise<ClaimSeatResponse> {
  return request(`/v1/rooms/${roomId}/seats`, {
    method: 'POST',
    body: JSON.stringify({
      invite_token: inviteToken,
      display_name: displayName,
      kind,
      ...(seat !== undefined ? { seat } : {}),
    }),
  });
}

export function getView(roomId: string, seatToken: string): Promise<Observation> {
  return request(`/v1/rooms/${roomId}/view`, {
    headers: { Authorization: `Bearer ${seatToken}` },
  });
}

export function getEvents(roomId: string, since: number): Promise<EventsResponse> {
  return request(`/v1/rooms/${roomId}/events?since=${since}`);
}

export function postAction(
  roomId: string,
  seatToken: string,
  requestId: string,
  action: Action,
  tableTalk?: string
): Promise<ActionResponse> {
  return request(`/v1/rooms/${roomId}/actions`, {
    method: 'POST',
    body: JSON.stringify({
      seat_token: seatToken,
      request_id: requestId,
      action,
      ...(tableTalk ? { table_talk: tableTalk } : {}),
    }),
  });
}
