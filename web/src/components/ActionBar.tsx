import { useState } from 'react';
import type { Action, LegalAction } from '../lib/types';

// Renders strictly from legal_actions — never construct an action the server didn't offer.
export default function ActionBar({
  legalActions,
  disabled,
  onSubmit,
}: {
  legalActions: LegalAction[];
  disabled: boolean;
  onSubmit: (action: Action) => void;
}) {
  const raiseSpec = legalActions.find((a) => a.type === 'raise');
  const [raiseTo, setRaiseTo] = useState<number | null>(null);

  const to = raiseTo ?? raiseSpec?.min_to ?? 0;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-gray-400 bg-white p-3">
      {legalActions.map((a) => {
        if (a.type === 'raise') return null;
        const label =
          a.type === 'call' && a.amount !== undefined ? `call ${a.amount}` : a.type;
        return (
          <button
            key={a.type}
            disabled={disabled}
            className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50"
            onClick={() => onSubmit({ type: a.type })}
          >
            {label}
          </button>
        );
      })}

      {raiseSpec && raiseSpec.min_to != null && raiseSpec.max_to != null && (
        <div className="flex items-center gap-2">
          <input
            type="range"
            min={raiseSpec.min_to}
            max={raiseSpec.max_to}
            value={to}
            disabled={disabled}
            onChange={(e) => setRaiseTo(Number(e.target.value))}
          />
          <span className="w-24 font-mono text-sm">
            raise to {to} ({raiseSpec.min_to}-{raiseSpec.max_to})
          </span>
          <button
            disabled={disabled}
            className="rounded bg-red-600 px-3 py-2 text-white disabled:opacity-50"
            onClick={() => onSubmit({ type: 'raise', to })}
          >
            raise
          </button>
        </div>
      )}
    </div>
  );
}
