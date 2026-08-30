import type { EventBase } from '../protocol'

function describe(ev: EventBase): string {
  switch (ev.type) {
    case 'room_created':
      return 'Room created'
    case 'seat_joined':
      return `${ev.name} joined seat ${(ev.seat as number) + 1}`
    case 'hand_started':
      return `Hand #${ev.hand_no} started`
    case 'blinds_posted': {
      const postings = ev.postings as { seat: number; amount: number; kind: string }[]
      return postings.map((p) => `seat ${p.seat + 1} posted ${p.kind} ${p.amount}`).join(', ')
    }
    case 'hole_cards_dealt':
      return `Hole cards dealt`
    case 'action_required':
      return `Seat ${(ev.seat as number) + 1} to act`
    case 'action_taken': {
      const action = ev.action as { type: string; to?: number }
      const label = action.type === 'raise' ? `raised to ${action.to}` : action.type
      return `seat ${(ev.seat as number) + 1} ${label}`
    }
    case 'board_dealt':
      return `${ev.street}: ${(ev.cards as string[]).join(' ')}`
    case 'table_talk':
      return `${ev.name}: "${ev.text}"`
    case 'showdown': {
      const reveals = ev.reveals as { seat: number; hole: string[]; description: string }[]
      return reveals
        .map((r) => `seat ${r.seat + 1} shows ${r.hole.join(' ')} (${r.description})`)
        .join(', ')
    }
    case 'pot_awarded': {
      const pots = ev.pots as {
        index: number
        amount: number
        awards: { seat: number; amount: number }[]
        reason: string
      }[]
      return pots
        .map(
          (p) =>
            `pot ${p.index} (${p.amount}, ${p.reason}) -> ${p.awards
              .map((a) => `seat ${a.seat + 1}: ${a.amount}`)
              .join(', ')}`,
        )
        .join(' | ')
    }
    case 'hand_complete':
      return `Hand #${ev.hand_no} complete`
    case 'seat_timed_out':
      return `Seat ${(ev.seat as number) + 1} timed out${ev.forced_action ? ` (${(ev.forced_action as { type: string }).type})` : ''}`
    default:
      return ev.type
  }
}

export function EventFeed({ events }: { events: EventBase[] }) {
  return (
    <div className="flex flex-col gap-1 overflow-y-auto text-xs">
      {events.map((ev) => (
        <div key={ev.seq} className="flex gap-2">
          <span className="text-neutral-600 shrink-0">{ev.seq}</span>
          <span className="text-neutral-300">{describe(ev)}</span>
        </div>
      ))}
    </div>
  )
}
