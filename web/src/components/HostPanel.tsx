import { useState } from 'react';
import { ApiError, claimSeat, startRoom } from '../lib/api';
import type { OpenSeatSummary, SeatKind } from '../lib/types';

// Only rendered when this browser holds the room's host_token (i.e. it created
// the room) — see docs/PROTOCOL.md §1, host_token is not shareable.
export default function HostPanel({
  roomId,
  hostToken,
  inviteToken,
  seats,
  allSeatsClaimed,
  onSelfClaimed,
}: {
  roomId: string;
  hostToken: string;
  inviteToken: string | null;
  seats: OpenSeatSummary[];
  allSeatsClaimed: boolean;
  onSelfClaimed?: (seatToken: string) => void;
}) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [claimingSelf, setClaimingSelf] = useState(false);

  const [aiKind, setAiKind] = useState<SeatKind>('model');
  const [aiName, setAiName] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [aiApiKey, setAiApiKey] = useState('');
  const [aiSeatToken, setAiSeatToken] = useState<{ seat: number; token: string } | null>(null);
  const [claimingAi, setClaimingAi] = useState(false);

  const openSeats = seats.filter((s) => s.status === 'open');

  async function handleStart() {
    setStarting(true);
    setError(null);
    try {
      await startRoom(roomId, hostToken);
    } catch (e) {
      setError(e instanceof ApiError ? e.body.reason : String(e));
    } finally {
      setStarting(false);
    }
  }

  async function handleSelfClaim(seatIndex: number) {
    if (!inviteToken || !name.trim() || !onSelfClaimed) return;
    setClaimingSelf(true);
    setError(null);
    try {
      const res = await claimSeat(roomId, inviteToken, name.trim(), seatIndex, 'human');
      onSelfClaimed(res.seat_token);
    } catch (e) {
      setError(e instanceof ApiError ? e.body.reason : String(e));
    } finally {
      setClaimingSelf(false);
    }
  }

  async function handleAiClaim(seatIndex: number) {
    if (!inviteToken || !aiName.trim()) return;
    setClaimingAi(true);
    setError(null);
    try {
      const res = await claimSeat(roomId, inviteToken, aiName.trim(), seatIndex, aiKind);
      setAiSeatToken({ seat: res.seat_index, token: res.seat_token });
    } catch (e) {
      setError(e instanceof ApiError ? e.body.reason : String(e));
    } finally {
      setClaimingAi(false);
    }
  }

  return (
    <div className="mb-4 space-y-4 rounded border border-blue-400 bg-blue-50 p-3 text-sm">
      <div className="font-bold">Host controls</div>
      {error && <div className="rounded border border-red-400 bg-red-50 p-2 text-red-700">{error}</div>}

      <button
        className="rounded bg-green-600 px-3 py-2 text-white disabled:opacity-50"
        disabled={starting || !allSeatsClaimed}
        onClick={handleStart}
        title={allSeatsClaimed ? '' : 'all seats must be claimed first'}
      >
        {starting ? 'starting...' : 'start game'}
      </button>

      {onSelfClaimed && inviteToken && openSeats.length > 0 && (
        <div className="space-y-2 border-t border-blue-200 pt-3">
          <div className="font-semibold">Claim a seat for yourself</div>
          <input
            className="w-full rounded border border-gray-400 p-2"
            placeholder="your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            {openSeats.map((s) => (
              <button
                key={s.index}
                disabled={claimingSelf || !name.trim()}
                className="rounded bg-gray-700 px-2 py-1 text-white disabled:opacity-50"
                onClick={() => handleSelfClaim(s.index)}
              >
                seat {s.index}
              </button>
            ))}
          </div>
        </div>
      )}

      {inviteToken && openSeats.length > 0 && (
        <div className="space-y-2 border-t border-blue-200 pt-3">
          <div className="font-semibold">Add a model / agent seat</div>
          <p className="text-xs text-gray-600">
            This reserves the seat and issues a seat_token, exactly like a human join — the room
            server accepts <code>kind: "model"</code> today. It does <strong>not</strong> start any
            model playing: <code>packages/agent_runtime</code> has a <code>ModelSeatDriver</code>{' '}
            class that can drive this seat via the same REST API, but no CLI/script currently
            exists to launch it — that has to be run separately by whoever owns that seat.
          </p>
          <div className="flex gap-2">
            <select
              className="rounded border border-gray-400 p-2"
              value={aiKind}
              onChange={(e) => setAiKind(e.target.value as SeatKind)}
            >
              <option value="model">model</option>
              <option value="agent">agent</option>
            </select>
            <input
              className="flex-1 rounded border border-gray-400 p-2"
              placeholder="display name, e.g. GPT-5"
              value={aiName}
              onChange={(e) => setAiName(e.target.value)}
            />
          </div>
          <input
            className="w-full rounded border border-gray-400 p-2"
            placeholder="model id (for your own reference only, e.g. openai/gpt-4o)"
            value={aiModel}
            onChange={(e) => setAiModel(e.target.value)}
          />
          <input
            type="password"
            className="w-full rounded border border-gray-400 p-2"
            placeholder="API key (kept in this browser only — never sent to the server)"
            value={aiApiKey}
            onChange={(e) => setAiApiKey(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            {openSeats.map((s) => (
              <button
                key={s.index}
                disabled={claimingAi || !aiName.trim()}
                className="rounded bg-purple-700 px-2 py-1 text-white disabled:opacity-50"
                onClick={() => handleAiClaim(s.index)}
              >
                seat {s.index}
              </button>
            ))}
          </div>
          {aiSeatToken && (
            <div className="rounded border border-purple-400 bg-purple-50 p-2 font-mono text-xs">
              seat {aiSeatToken.seat} seat_token: {aiSeatToken.token}
              <div className="mt-1 font-sans text-gray-600">
                Hand this token to the process running <code>ModelSeatDriver</code> for
                {aiModel ? ` "${aiModel}"` : ' this seat'}. The API key you typed above was kept
                only in this form's local state — pass it to that process yourself, not through
                this page.
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
