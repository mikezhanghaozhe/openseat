import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { claimSeat, getRoom } from '../lib/api';
import { setSeatToken } from '../lib/storage';
import type { RoomSummary } from '../lib/types';

// The server keys everything off room_id (see docs/PROTOCOL.md §6); a shareable
// join link therefore carries both room_id and invite_token.
export default function JoinPage() {
  const { roomId, inviteToken } = useParams<{ roomId: string; inviteToken: string }>();
  const navigate = useNavigate();

  const [room, setRoom] = useState<RoomSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [claiming, setClaiming] = useState<number | null>(null);

  useEffect(() => {
    if (!roomId) return;
    let cancelled = false;
    getRoom(roomId)
      .then((r) => {
        if (!cancelled) setRoom(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [roomId]);

  async function handleClaim(seatIndex: number) {
    if (!roomId || !inviteToken) return;
    if (!name.trim()) {
      setError('enter a name first');
      return;
    }
    setClaiming(seatIndex);
    setError(null);
    try {
      const res = await claimSeat(roomId, inviteToken, name.trim(), seatIndex);
      setSeatToken(roomId, res.seat_token);
      navigate(`/room/${roomId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClaiming(null);
    }
  }

  if (!roomId || !inviteToken) {
    return <div className="p-6 text-red-600">missing room id or invite token in url</div>;
  }

  return (
    <div className="mx-auto max-w-md p-6">
      <h1 className="mb-4 text-xl font-bold">Join room {roomId}</h1>

      {error && (
        <div className="mb-4 rounded border border-red-400 bg-red-50 p-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <label className="mb-4 block text-sm">
        Your name
        <input
          className="mt-1 w-full rounded border border-gray-400 p-2"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="mike"
        />
      </label>

      {!room && !error && <div>loading room...</div>}

      {room && (
        <ul className="space-y-2">
          {room.seats.map((s) => (
            <li key={s.index} className="flex items-center justify-between rounded border border-gray-300 p-2">
              <span>
                Seat {s.index} — {s.status === 'open' ? 'open' : `taken by ${s.name ?? '?'}`}
              </span>
              {s.status === 'open' ? (
                <button
                  className="rounded bg-green-600 px-3 py-1 text-white disabled:opacity-50"
                  disabled={claiming !== null}
                  onClick={() => handleClaim(s.index)}
                >
                  {claiming === s.index ? 'claiming...' : 'claim'}
                </button>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
