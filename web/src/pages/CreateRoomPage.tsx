import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRoom } from '../lib/api';
import { setHostToken, setInviteToken } from '../lib/storage';

export default function CreateRoomPage() {
  const navigate = useNavigate();

  const [seats, setSeats] = useState(4);
  const [sb, setSb] = useState(25);
  const [bb, setBb] = useState(50);
  const [startingStack, setStartingStack] = useState(5000);
  const [turnSeconds, setTurnSeconds] = useState(30);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ roomId: string; joinUrl: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      const res = await createRoom({ seats, sb, bb, starting_stack: startingStack, turn_seconds: turnSeconds });
      setHostToken(res.room_id, res.host_token);
      setInviteToken(res.room_id, res.invite_token);
      const joinUrl = `${window.location.origin}/join/${res.room_id}/${res.invite_token}`;
      setCreated({ roomId: res.room_id, joinUrl });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  if (created) {
    return (
      <div className="mx-auto max-w-md p-6">
        <h1 className="mb-4 text-xl font-bold">Room created</h1>
        <p className="mb-2 text-sm text-gray-600">Share this link so others can join:</p>
        <div className="mb-2 flex gap-2">
          <input readOnly className="w-full rounded border border-gray-400 p-2 font-mono text-xs" value={created.joinUrl} />
          <button
            className="shrink-0 rounded bg-gray-700 px-3 py-1 text-white"
            onClick={() => {
              navigator.clipboard.writeText(created.joinUrl);
              setCopied(true);
            }}
          >
            {copied ? 'copied' : 'copy'}
          </button>
        </div>
        <button
          className="mt-4 w-full rounded bg-green-600 px-3 py-2 text-white"
          onClick={() => navigate(`/room/${created.roomId}`)}
        >
          go to room
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md p-6">
      <h1 className="mb-4 text-xl font-bold">Create a room</h1>

      {error && (
        <div className="mb-4 rounded border border-red-400 bg-red-50 p-2 text-sm text-red-700">{error}</div>
      )}

      <div className="space-y-3">
        <label className="block text-sm">
          Seats
          <input
            type="number"
            min={2}
            max={9}
            className="mt-1 w-full rounded border border-gray-400 p-2"
            value={seats}
            onChange={(e) => setSeats(Number(e.target.value))}
          />
        </label>
        <label className="block text-sm">
          Small blind
          <input
            type="number"
            min={1}
            className="mt-1 w-full rounded border border-gray-400 p-2"
            value={sb}
            onChange={(e) => setSb(Number(e.target.value))}
          />
        </label>
        <label className="block text-sm">
          Big blind
          <input
            type="number"
            min={1}
            className="mt-1 w-full rounded border border-gray-400 p-2"
            value={bb}
            onChange={(e) => setBb(Number(e.target.value))}
          />
        </label>
        <label className="block text-sm">
          Starting stack
          <input
            type="number"
            min={bb}
            className="mt-1 w-full rounded border border-gray-400 p-2"
            value={startingStack}
            onChange={(e) => setStartingStack(Number(e.target.value))}
          />
        </label>
        <label className="block text-sm">
          Turn seconds
          <input
            type="number"
            min={5}
            className="mt-1 w-full rounded border border-gray-400 p-2"
            value={turnSeconds}
            onChange={(e) => setTurnSeconds(Number(e.target.value))}
          />
        </label>
      </div>

      <button
        className="mt-4 w-full rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50"
        disabled={creating}
        onClick={handleCreate}
      >
        {creating ? 'creating...' : 'create room'}
      </button>
    </div>
  );
}
