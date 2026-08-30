import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { loadSeatToken } from '../api'
import { useRoomSocket } from '../useRoomSocket'
import { SeatCard } from '../components/SeatCard'
import { Card } from '../components/Card'
import { ActionBar } from '../components/ActionBar'
import { EventFeed } from '../components/EventFeed'
import { PotDisplay } from '../components/PotDisplay'
import { Chat } from '../components/Chat'
import type { Action } from '../protocol'

function useClockRemaining(deadlineMs: number | null): number | null {
  const [remaining, setRemaining] = useState<number | null>(null)
  useEffect(() => {
    if (deadlineMs == null) {
      setRemaining(null)
      return
    }
    const tick = () => setRemaining(Math.max(0, Math.round((deadlineMs - Date.now()) / 1000)))
    tick()
    const id = setInterval(tick, 250)
    return () => clearInterval(id)
  }, [deadlineMs])
  return remaining
}

export function RoomScreen() {
  const { roomId } = useParams<{ roomId: string }>()
  const seatToken = roomId ? loadSeatToken(roomId) : null
  const [tableTalk, setTableTalk] = useState('')
  const { observation, events, clockDeadline, wsError, connected, sendAction } = useRoomSocket(
    roomId ?? '',
    seatToken,
  )
  const remaining = useClockRemaining(clockDeadline)

  const reasoningBySeat = useMemo(() => {
    const map = new Map<number, string>()
    for (const ev of events) {
      if (ev.type === 'table_talk') {
        map.set(ev.seat as number, ev.text as string)
      }
    }
    return map
  }, [events])

  if (!roomId) return null

  if (!seatToken) {
    return (
      <div className="min-h-screen flex items-center justify-center text-neutral-400 text-sm">
        No seat found for this room in this browser. Use your invite link to join.
      </div>
    )
  }

  if (!observation) {
    return (
      <div className="min-h-screen flex items-center justify-center text-neutral-500">
        {connected ? 'Loading table...' : 'Connecting...'}
      </div>
    )
  }

  const { you, seats, board, pot_total, phase, button, legal_actions, chat } = observation

  function handleAct(action: Action) {
    sendAction(action, tableTalk.trim() || undefined)
    setTableTalk('')
  }

  return (
    <div className="min-h-screen flex flex-col">
      <div className="flex items-center gap-4 px-4 py-3 border-b border-neutral-800 text-sm">
        <span className="font-bold">openseat</span>
        <span className="text-neutral-500">room {roomId}</span>
        <span className="text-neutral-500">hand #{observation.hand_no}</span>
        <span className="text-neutral-500 uppercase">{phase}</span>
        {!connected && <span className="text-amber-500 ml-auto">reconnecting...</span>}
      </div>

      <div className="flex flex-1 min-h-0">
        <div className="flex-1 flex flex-col items-center justify-center gap-6 p-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 w-full max-w-4xl">
            {seats
              .filter((s) => s.seat !== you.seat)
              .map((s) => (
                <SeatCard
                  key={s.seat}
                  seat={s}
                  isYou={false}
                  isActing={observation.to_act === s.seat}
                  isButton={button === s.seat}
                  reasoning={s.kind !== 'human' ? reasoningBySeat.get(s.seat) : undefined}
                  hole={s.revealed?.hole}
                />
              ))}
          </div>

          <div className="flex gap-2">
            {board.map((c, i) => (
              <Card key={i} card={c} />
            ))}
          </div>

          <div className="text-sm text-neutral-400">pot {pot_total.toLocaleString()}</div>

          <PotDisplay events={events} seats={seats} />

          <div className="mt-2">
            <SeatCard
              seat={{
                seat: you.seat,
                name: you.name,
                kind: 'human',
                stack: you.stack,
                committed_street: you.committed_street,
                status: you.status,
                last_action: seats.find((s) => s.seat === you.seat)?.last_action ?? null,
              }}
              isYou
              isActing={observation.to_act === you.seat}
              isButton={button === you.seat}
              hole={you.hole}
            />
          </div>

          {observation.to_act === you.seat && (
            <div className="w-full max-w-2xl border border-neutral-800 rounded-lg">
              {remaining != null && (
                <div className="text-xs text-amber-500 px-4 pt-2">{remaining}s remaining</div>
              )}
              <ActionBar legalActions={legal_actions} onAct={handleAct} />
            </div>
          )}

          {wsError && (
            <div className="text-xs text-red-400 border border-red-900 rounded px-3 py-2">
              {wsError.code}: {wsError.reason}
            </div>
          )}
        </div>

        <div className="w-80 border-l border-neutral-800 flex flex-col p-4 gap-4 min-h-0">
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2">Feed</div>
            <EventFeed events={events} />
          </div>
          <div className="border-t border-neutral-800 pt-3">
            <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2">Chat</div>
            <Chat messages={chat} text={tableTalk} onTextChange={setTableTalk} />
            <div className="text-[10px] text-neutral-600 mt-1">
              Sent as table talk with your next action.
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
