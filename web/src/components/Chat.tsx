import type { ChatMessage } from '../protocol'

export function Chat({
  messages,
  text,
  onTextChange,
}: {
  messages: ChatMessage[]
  text: string
  onTextChange: (text: string) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-2 overflow-y-auto max-h-40">
        {messages.map((m) => (
          <div key={m.seq} className="text-xs">
            <span className="text-neutral-300">{m.name}</span>{' '}
            <span className="text-neutral-500">{m.text}</span>
          </div>
        ))}
      </div>
      <input
        value={text}
        onChange={(e) => onTextChange(e.target.value)}
        placeholder="Message the table..."
        className="bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-xs outline-none focus:border-cyan-500"
      />
    </div>
  )
}
