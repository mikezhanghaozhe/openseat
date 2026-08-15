import type { ResultResponse } from '../lib/types';

export default function ResultBanner({ result }: { result: ResultResponse | null }) {
  return (
    <div className="mb-4 rounded border border-amber-400 bg-amber-50 p-3 text-sm">
      <div className="font-bold">Hand complete{result ? ` — hand ${result.hand_no}` : ''}</div>
      {result ? (
        <>
          <div className="mt-1">final stacks: {result.final_stacks.join(', ')}</div>
          {result.pots.map((p) => (
            <div key={p.index}>
              pot {p.index} ({p.reason}): {p.awards.map((a) => `seat ${a.seat} +${a.amount}`).join(', ')}
            </div>
          ))}
          {result.showdown.length > 0 && (
            <div className="mt-1">
              showdown: {result.showdown.map((r) => `seat ${r.seat} ${r.hole.join(' ')} (${r.description})`).join('; ')}
            </div>
          )}
        </>
      ) : (
        <div className="mt-1 text-gray-600">loading result...</div>
      )}
    </div>
  );
}
