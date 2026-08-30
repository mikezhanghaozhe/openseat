import { Card } from './Card'
import type { SeatView } from '../protocol'

const KIND_LABEL: Record<string, string> = { human: 'HUMAN', model: 'MODEL', agent: 'AGENT' }
const KIND_COLOR: Record<string, string> = {
  human: 'text-amber-400 border-amber-800',
  model: 'text-cyan-400 border-cyan-800',
  agent: 'text-purple-400 border-purple-800',
}

function actionLabel(a: SeatView['last_action']): string | null {
  if (!a) return null
  if (a.type === 'raise') return `raised to ${a.to}`
  return a.type
}

export function SeatCard({
  seat,
  isYou,
  isActing,
  isButton,
  reasoning,
  hole,
}: {
  seat: SeatView
  isYou: boolean
  isActing: boolean
  isButton: boolean
  reasoning?: string
  // Other seats carry no hole-card field at all (redacted server-side) except after a
  // showdown reveal (seat.revealed.hole) — the caller decides what's visible, this
  // component only ever renders what it's handed.
  hole?: string[]
}) {
  return (
    <div
      className={`rounded-lg border p-3 bg-neutral-950 ${
        isActing ? 'border-cyan-500' : 'border-neutral-800'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-md border border-neutral-700 flex items-center justify-center text-xs shrink-0">
            {seat.name[0]?.toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate">{isYou ? `${seat.name} (you)` : seat.name}</span>
              {isButton && (
                <span className="w-4 h-4 rounded-full bg-neutral-200 text-black text-[10px] flex items-center justify-center shrink-0">
                  D
                </span>
              )}
            </div>
            <div className="flex gap-1 mt-0.5">
              <span
                className={`text-[10px] uppercase tracking-wider border rounded px-1 ${KIND_COLOR[seat.kind]}`}
              >
                {KIND_LABEL[seat.kind]}
              </span>
              <span
                className={`text-[10px] uppercase tracking-wider border rounded px-1 ${
                  seat.status === 'folded'
                    ? 'text-neutral-500 border-neutral-700'
                    : seat.status === 'all_in'
                      ? 'text-orange-400 border-orange-800'
                      : 'text-emerald-400 border-emerald-800'
                }`}
              >
                {seat.status}
              </span>
            </div>
          </div>
        </div>
        {hole && (
          <div className="flex gap-1 shrink-0">
            {hole.map((c, i) => (
              <Card key={i} card={c} />
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between mt-2">
        <span className="text-lg font-semibold">{seat.stack.toLocaleString()}</span>
        <span className="text-xs text-neutral-500">{actionLabel(seat.last_action)}</span>
      </div>
      {reasoning && (
        <div className="mt-2 text-xs text-neutral-400 border-l-2 border-neutral-700 pl-2">
          {reasoning}
        </div>
      )}
    </div>
  )
}
