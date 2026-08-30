import type { EventBase, PotAward, SeatView } from '../protocol'

export function PotDisplay({ events, seats }: { events: EventBase[]; seats: SeatView[] }) {
  const potAwarded = [...events].reverse().find((e) => e.type === 'pot_awarded')
  if (!potAwarded) return null
  const pots = potAwarded.pots as PotAward[]
  const nameFor = (seat: number) => seats.find((s) => s.seat === seat)?.name ?? `seat ${seat + 1}`

  return (
    <div className="flex gap-2 flex-wrap justify-center">
      {pots.map((p) => (
        <div
          key={p.index}
          className="text-xs border border-neutral-700 rounded px-2 py-1 bg-neutral-900"
        >
          <span className="uppercase text-neutral-500">
            {pots.length > 1 ? (p.index === 0 ? 'MAIN' : `SIDE ${p.index}`) : 'POT'}
          </span>{' '}
          {p.amount.toLocaleString()} →{' '}
          {p.awards.map((a, i) => (
            <span key={a.seat}>
              {i > 0 ? ', ' : ''}
              {nameFor(a.seat)} ({a.amount.toLocaleString()})
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}
