const SUIT_SYMBOL: Record<string, string> = { c: '♣', d: '♦', h: '♥', s: '♠' }
const SUIT_COLOR: Record<string, string> = {
  c: 'text-emerald-600',
  d: 'text-blue-500',
  h: 'text-red-500',
  s: 'text-black',
}

export function Card({ card }: { card: string }) {
  if (card === '??') {
    return <div className="w-12 h-16 rounded-md bg-neutral-800 border border-neutral-700" />
  }
  const rank = card[0]
  const suit = card[1]
  return (
    <div className="w-12 h-16 rounded-md bg-neutral-100 flex flex-col items-center justify-center leading-none">
      <span className={`text-lg font-bold ${SUIT_COLOR[suit] ?? 'text-black'}`}>{rank === 'T' ? '10' : rank}</span>
      <span className={`text-lg ${SUIT_COLOR[suit] ?? 'text-black'}`}>{SUIT_SYMBOL[suit] ?? suit}</span>
    </div>
  )
}
