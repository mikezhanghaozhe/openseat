import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { v4 as uuidv4 } from 'uuid';
import { ApiError, getEvents, getResult, getRoom, getView, postAction } from '../lib/api';
import { getHostToken, getInviteToken, getSeatToken, setSeatToken } from '../lib/storage';
import ActionBar from '../components/ActionBar';
import EventFeed from '../components/EventFeed';
import HostPanel from '../components/HostPanel';
import ResultBanner from '../components/ResultBanner';
import Seat from '../components/Seat';
import type { Action, LegalAction, Observation, ResultResponse, RoomEvent, RoomSummary } from '../lib/types';

const POLL_MS = 1000;

function seatPosition(index: number, total: number): React.CSSProperties {
  const angle = (2 * Math.PI * index) / total - Math.PI / 2;
  const rx = 42; // % of container width
  const ry = 38; // % of container height
  const left = 50 + rx * Math.cos(angle);
  const top = 50 + ry * Math.sin(angle);
  return { left: `${left}%`, top: `${top}%` };
}

function useHandCompleteResult(roomId: string | undefined, handComplete: boolean) {
  const [result, setResult] = useState<ResultResponse | null>(null);
  useEffect(() => {
    if (!roomId || !handComplete) return;
    let cancelled = false;
    getResult(roomId).then((r) => {
      if (!cancelled) setResult(r);
    }).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [roomId, handComplete]);
  return result;
}

export default function RoomPage() {
  const { roomId } = useParams<{ roomId: string }>();
  const [seatToken, setSeatTokenState] = useState<string | null>(() => (roomId ? getSeatToken(roomId) : null));
  const hostToken = roomId ? getHostToken(roomId) : null;
  const inviteToken = roomId ? getInviteToken(roomId) : null;

  const [events, setEvents] = useState<RoomEvent[]>([]);
  const latestSeqRef = useRef(0);

  const pollEvents = useCallback(async () => {
    if (!roomId) return;
    try {
      const res = await getEvents(roomId, latestSeqRef.current);
      if (res.events.length > 0) {
        setEvents((prev) => [...prev, ...res.events]);
        latestSeqRef.current = res.latest_seq;
      }
    } catch {
      // events are non-critical for the demo
    }
  }, [roomId]);

  useEffect(() => {
    if (!roomId) return;
    pollEvents();
    const t = setInterval(pollEvents, POLL_MS);
    return () => clearInterval(t);
  }, [roomId, pollEvents]);

  function handleSelfClaimed(token: string) {
    if (!roomId) return;
    setSeatToken(roomId, token);
    setSeatTokenState(token);
  }

  if (!roomId) return <div className="p-6 text-red-600">missing room id</div>;

  if (!seatToken) {
    return (
      <SpectatorRoom
        roomId={roomId}
        hostToken={hostToken}
        inviteToken={inviteToken}
        events={events}
        onSelfClaimed={handleSelfClaimed}
      />
    );
  }

  return (
    <PlayerRoom
      roomId={roomId}
      seatToken={seatToken}
      hostToken={hostToken}
      inviteToken={inviteToken}
      events={events}
    />
  );
}

function SpectatorRoom({
  roomId,
  hostToken,
  inviteToken,
  events,
  onSelfClaimed,
}: {
  roomId: string;
  hostToken: string | null;
  inviteToken: string | null;
  events: RoomEvent[];
  onSelfClaimed: (seatToken: string) => void;
}) {
  const [summary, setSummary] = useState<RoomSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const s = await getRoom(roomId);
      setSummary(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [roomId]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  const handComplete = summary?.status === 'complete';
  const result = useHandCompleteResult(roomId, handComplete);

  if (error) return <div className="p-6 text-red-600">error: {error}</div>;
  if (!summary) return <div className="p-6">loading...</div>;

  return (
    <div className="mx-auto max-w-2xl p-4">
      <div className="mb-2 flex justify-between text-sm text-gray-600">
        <span>room {summary.room_id} (spectating)</span>
        <span>
          hand {summary.hand_no} — phase {summary.phase}
        </span>
      </div>

      {handComplete && <ResultBanner result={result} />}

      {hostToken && (
        <HostPanel
          roomId={roomId}
          hostToken={hostToken}
          inviteToken={inviteToken}
          seats={summary.seats}
          allSeatsClaimed={summary.seats.every((s) => s.status === 'claimed')}
          onSelfClaimed={onSelfClaimed}
        />
      )}

      <ul className="mb-4 space-y-1 text-sm">
        {summary.seats.map((s) => (
          <li key={s.index} className="rounded border border-gray-300 p-2">
            seat {s.index} — {s.status === 'open' ? 'open' : `${s.name} (${s.kind})`}
          </li>
        ))}
      </ul>

      <EventFeed events={events} />
    </div>
  );
}

function PlayerRoom({
  roomId,
  seatToken,
  hostToken,
  inviteToken,
  events,
}: {
  roomId: string;
  seatToken: string;
  hostToken: string | null;
  inviteToken: string | null;
  events: RoomEvent[];
}) {
  const [obs, setObs] = useState<Observation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [overrideLegalActions, setOverrideLegalActions] = useState<LegalAction[] | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const pollView = useCallback(async () => {
    try {
      const view = await getView(roomId, seatToken);
      setObs(view);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [roomId, seatToken]);

  useEffect(() => {
    pollView();
    const t = setInterval(pollView, POLL_MS);
    return () => clearInterval(t);
  }, [pollView]);

  // The host may have claimed a seat themselves, which is the common flow
  // (create room -> take a seat -> share the link) — they still need to see
  // "start game" and be able to add more seats, so poll the room summary too
  // until the game actually starts.
  const [summary, setSummary] = useState<RoomSummary | null>(null);
  useEffect(() => {
    if (!hostToken) return;
    let stopped = false;
    let t: ReturnType<typeof setInterval> | undefined;
    const poll = async () => {
      try {
        const s = await getRoom(roomId);
        if (stopped) return;
        setSummary(s);
        if (s.status !== 'waiting' && t) {
          clearInterval(t);
        }
      } catch {
        // non-critical — the host panel just won't show seat state
      }
    };
    poll();
    t = setInterval(poll, POLL_MS);
    return () => {
      stopped = true;
      if (t) clearInterval(t);
    };
  }, [roomId, hostToken]);

  const handComplete = obs?.phase === 'hand_complete';
  const result = useHandCompleteResult(roomId, handComplete);

  async function handleAction(action: Action) {
    setSubmitting(true);
    setActionError(null);
    setOverrideLegalActions(null);
    try {
      await postAction(roomId, seatToken, uuidv4(), action);
      await pollView();
    } catch (e) {
      if (e instanceof ApiError && e.body.error === 'illegal_action') {
        setActionError(e.body.reason);
        if (e.body.legal_actions) {
          setOverrideLegalActions(e.body.legal_actions as LegalAction[]);
        }
      } else {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (error) return <div className="p-6 text-red-600">error: {error}</div>;
  if (!obs) return <div className="p-6">loading...</div>;

  const isYourTurn = obs.to_act !== null && obs.to_act === obs.you.seat;
  const legalActions = overrideLegalActions ?? obs.legal_actions;

  return (
    <div className="mx-auto max-w-4xl p-4">
      <div className="mb-2 flex justify-between text-sm text-gray-600">
        <span>room {obs.room_id}</span>
        <span>
          hand {obs.hand_no} — phase {obs.phase} — seq {obs.seq}
        </span>
      </div>

      {handComplete && <ResultBanner result={result} />}

      {hostToken && summary && summary.status === 'waiting' && (
        <HostPanel
          roomId={roomId}
          hostToken={hostToken}
          inviteToken={inviteToken}
          seats={summary.seats}
          allSeatsClaimed={summary.seats.every((s) => s.status === 'claimed')}
        />
      )}

      <div className="relative mb-4 h-96 w-full rounded-full border-4 border-green-800 bg-green-700">
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-center text-white">
          <div className="flex gap-1">
            {obs.board.map((c, i) => (
              <span key={i} className="rounded bg-white px-1 font-mono text-black">
                {c}
              </span>
            ))}
          </div>
          <div className="mt-1 text-sm">pot: {obs.pot_total}</div>
        </div>

        {obs.seats.map((s) => (
          <Seat
            key={s.seat}
            seat={s.seat === obs.you.seat ? { ...s, hole: obs.you.hole } : s}
            isYou={s.seat === obs.you.seat}
            isToAct={obs.to_act === s.seat}
            isButton={obs.button === s.seat}
            style={seatPosition(s.seat, obs.seats.length)}
          />
        ))}
      </div>

      {isYourTurn && (
        <div className="mb-4">
          {actionError && (
            <div className="mb-2 rounded border border-red-400 bg-red-50 p-2 text-sm text-red-700">
              {actionError}
            </div>
          )}
          <ActionBar legalActions={legalActions} disabled={submitting} onSubmit={handleAction} />
        </div>
      )}

      <EventFeed events={events} />
    </div>
  );
}
