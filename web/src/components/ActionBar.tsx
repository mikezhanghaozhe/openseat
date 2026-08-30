import { useEffect, useState } from 'react'
import type { Action, LegalAction } from '../protocol'

export function ActionBar({
  legalActions,
  onAct,
}: {
  legalActions: LegalAction[]
  onAct: (action: Action) => void
}) {
  const raiseAction = legalActions.find((a) => a.type === 'raise') as
    | { type: 'raise'; min_to: number; max_to: number }
    | undefined
  const callAction = legalActions.find((a) => a.type === 'call') as
    | { type: 'call'; amount: number }
    | undefined
  const hasCheck = legalActions.some((a) => a.type === 'check')
  const hasFold = legalActions.some((a) => a.type === 'fold')
  const hasShow = legalActions.some((a) => a.type === 'show')
  const hasMuck = legalActions.some((a) => a.type === 'muck')

  const [raiseTo, setRaiseTo] = useState(raiseAction?.min_to ?? 0)

  useEffect(() => {
    if (raiseAction) setRaiseTo(raiseAction.min_to)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raiseAction?.min_to, raiseAction?.max_to])

  if (legalActions.length === 0) {
    return <div className="text-sm text-neutral-500 px-4 py-3">Waiting...</div>
  }

  if (hasShow || hasMuck) {
    return (
      <div className="flex gap-3 p-4">
        {hasShow && (
          <button
            onClick={() => onAct({ type: 'show' })}
            className="bg-cyan-400 text-black font-medium rounded-md px-4 py-2"
          >
            Show
          </button>
        )}
        {hasMuck && (
          <button
            onClick={() => onAct({ type: 'muck' })}
            className="bg-neutral-800 border border-neutral-700 rounded-md px-4 py-2"
          >
            Muck
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-4 p-4 flex-wrap">
      {hasFold && (
        <button
          onClick={() => onAct({ type: 'fold' })}
          className="bg-red-950 border border-red-800 text-red-300 rounded-md px-4 py-2"
        >
          Fold
        </button>
      )}
      {hasCheck && (
        <button
          onClick={() => onAct({ type: 'check' })}
          className="bg-neutral-800 border border-neutral-700 rounded-md px-4 py-2"
        >
          Check
        </button>
      )}
      {callAction && (
        <button
          onClick={() => onAct({ type: 'call' })}
          className="bg-neutral-800 border border-neutral-700 rounded-md px-4 py-2"
        >
          Call {callAction.amount}
        </button>
      )}
      {raiseAction && (
        <>
          <button
            onClick={() => onAct({ type: 'raise', to: raiseTo })}
            className="bg-cyan-400 text-black font-medium rounded-md px-4 py-2"
          >
            Raise to {raiseTo.toLocaleString()}
          </button>
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <span className="text-xs text-neutral-500">min {raiseAction.min_to}</span>
            <input
              type="range"
              min={raiseAction.min_to}
              max={raiseAction.max_to}
              value={raiseTo}
              onChange={(e) => setRaiseTo(Number(e.target.value))}
              className="flex-1"
            />
            <span className="text-xs text-neutral-500">max {raiseAction.max_to} (all-in)</span>
          </div>
        </>
      )}
    </div>
  )
}
