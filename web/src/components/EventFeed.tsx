import type { Action, RoomEvent } from '../lib/types';

function describeAction(a: Action): string {
  if (a.type === 'raise') return `raises to ${a.to}`;
  return `${a.type}s`;
}

// One case per docs/PROTOCOL.md §5 event type, using the exact payload fields
// from packages/engine/types.py. Falls back to raw JSON for anything unmapped
// (e.g. future M2/M3 event types) rather than hiding it.
function describe(e: RoomEvent): string {
  const p = e.payload;
  switch (e.type) {
    case 'room_created':
      return `room created — ${p.game} (${p.seats_total} seats)`;
    case 'seat_joined':
      return `${p.name} joined seat ${p.seat} (${p.kind})`;
    case 'seat_left':
      return `seat ${p.seat} left (${p.reason})`;
    case 'hand_started':
      return `hand ${p.hand_no} started — button on seat ${p.button}`;
    case 'blinds_posted': {
      const postings = p.postings as { seat: number; amount: number; kind: string }[];
      return `blinds posted — ${postings.map((x) => `seat ${x.seat} ${x.kind} ${x.amount}`).join(', ')}`;
    }
    case 'hole_cards_dealt':
      return `hole cards dealt to seats ${(p.seats as number[]).join(', ')}`;
    case 'action_required':
      return `seat ${p.seat} to act`;
    case 'action_taken': {
      const action = p.action as Action;
      return `seat ${p.seat} ${describeAction(action)}${p.all_in ? ' (all-in)' : ''} — pot ${p.pot_after}`;
    }
    case 'board_dealt':
      return `${p.street} — ${(p.cards as string[]).join(' ')}`;
    case 'table_talk':
      return `${p.name} (seat ${p.seat}): "${p.text}"`;
    case 'showdown': {
      const reveals = p.reveals as { seat: number; hole: string[]; description: string }[];
      return `showdown — ${reveals.map((r) => `seat ${r.seat} ${r.hole.join(' ')} (${r.description})`).join('; ')}`;
    }
    case 'pot_awarded': {
      const pots = p.pots as { index: number; amount: number; reason: string; awards: { seat: number; amount: number }[] }[];
      return pots
        .map(
          (pot) =>
            `pot ${pot.index} (${pot.reason}) — ${pot.awards.map((a) => `seat ${a.seat} +${a.amount}`).join(', ')}`
        )
        .join('; ');
    }
    case 'hand_complete':
      return `hand ${p.hand_no} complete — final stacks: ${(p.stacks as number[]).join(', ')}`;
    case 'seat_timed_out':
      return `seat ${p.seat} timed out${p.forced_action ? ` — forced ${p.forced_action}` : ''}`;
    case 'room_complete':
      return `room complete — final stacks: ${(p.final_stacks as number[]).join(', ')}`;
    default:
      return `${e.type} ${JSON.stringify(p)}`;
  }
}

export default function EventFeed({ events }: { events: RoomEvent[] }) {
  return (
    <div className="h-64 overflow-y-auto rounded border border-gray-400 bg-white p-2 font-mono text-xs">
      {events.map((e) => (
        <div key={e.seq} className="border-b border-gray-100 py-0.5">
          <span className="text-gray-400">#{e.seq}</span> {describe(e)}
        </div>
      ))}
    </div>
  );
}
