import type { SeatPublic } from '../lib/types';

function describeAction(a: SeatPublic['last_action']): string {
  if (!a) return '';
  if (a.type === 'raise') return `raise to ${a.to}`;
  return a.type;
}

export default function Seat({
  seat,
  isYou,
  isToAct,
  isButton,
  style,
}: {
  seat: SeatPublic;
  isYou: boolean;
  isToAct: boolean;
  isButton: boolean;
  style: React.CSSProperties;
}) {
  const folded = seat.status === 'folded';
  return (
    <div
      style={style}
      className={[
        'absolute w-36 -translate-x-1/2 -translate-y-1/2 rounded border p-2 text-xs',
        isToAct ? 'border-yellow-400 bg-yellow-50 ring-2 ring-yellow-400' : 'border-gray-400 bg-white',
        folded ? 'opacity-50' : '',
      ].join(' ')}
    >
      <div className="flex items-center justify-between font-bold">
        <span>
          {seat.name} {isButton ? '(BTN)' : ''}
        </span>
        {isYou && <span className="text-blue-600">you</span>}
      </div>
      <div>stack: {seat.stack}</div>
      <div>street: {seat.committed_street}</div>
      <div>
        status: {seat.status}
        {seat.status === 'all_in' ? ' (all-in)' : ''}
      </div>
      <div className="h-4 truncate text-gray-600">{describeAction(seat.last_action)}</div>
      <div className="mt-1 flex gap-1">
        {isYou && seat.hole
          ? seat.hole.map((c, i) => (
              <span key={i} className="rounded bg-white border border-gray-500 px-1 font-mono">
                {c}
              </span>
            ))
          : seat.revealed
            ? seat.revealed.map((c, i) => (
                <span key={i} className="rounded bg-white border border-gray-500 px-1 font-mono">
                  {c}
                </span>
              ))
            : seat.status !== 'folded' && (
                <>
                  <span className="rounded bg-gray-700 px-2 text-gray-700">??</span>
                  <span className="rounded bg-gray-700 px-2 text-gray-700">??</span>
                </>
              )}
      </div>
    </div>
  );
}
