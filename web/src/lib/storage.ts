// seat_token / host_token / invite_token are secrets (docs/PROTOCOL.md §1) — never sent
// anywhere but the room-server itself, and never logged. localStorage here is the same
// trust boundary the browser already grants the page; it's the standard place a
// REST-only, no-accounts client keeps its bearer credentials.

const seatKey = (roomId: string) => `openseat:seat_token:${roomId}`;
const hostKey = (roomId: string) => `openseat:host_token:${roomId}`;
const inviteKey = (roomId: string) => `openseat:invite_token:${roomId}`;

export function getSeatToken(roomId: string): string | null {
  return localStorage.getItem(seatKey(roomId));
}

export function setSeatToken(roomId: string, seatToken: string): void {
  localStorage.setItem(seatKey(roomId), seatToken);
}

// Held only by whoever created the room (POST /rooms returns it once).
export function getHostToken(roomId: string): string | null {
  return localStorage.getItem(hostKey(roomId));
}

export function setHostToken(roomId: string, hostToken: string): void {
  localStorage.setItem(hostKey(roomId), hostToken);
}

// Also only known to the creator in this client — useful so the host can claim
// their own seat, or hand it to someone directly, without re-typing it.
export function getInviteToken(roomId: string): string | null {
  return localStorage.getItem(inviteKey(roomId));
}

export function setInviteToken(roomId: string, inviteToken: string): void {
  localStorage.setItem(inviteKey(roomId), inviteToken);
}
