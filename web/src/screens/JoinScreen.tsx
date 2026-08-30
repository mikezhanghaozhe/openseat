import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { claimSeat, getRoom, storeSeatToken, ApiError } from '../api'
import type { RoomSummary } from '../protocol'

export function JoinScreen() {
  const { inviteToken } = useParams<{ inviteToken: string }>()
  const [searchParams] = useSearchParams()
  const roomId = searchParams.get('room') ?? ''
  const navigate = useNavigate()

  const [room, setRoom] = useState<RoomSummary | null>(null)
  const [selectedSeat, setSelectedSeat] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!roomId) {
      setError('Missing room id — join link must include ?room=<room_id>.')
      return
    }
    getRoom(roomId)
      .then((r) => {
        setRoom(r)
        const firstOpen = r.seats.find((s) => s.status === 'open')
        if (firstOpen) setSelectedSeat(firstOpen.index)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [roomId])

  async function handleTakeSeat() {
    if (!inviteToken || selectedSeat == null || !name.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await claimSeat(roomId, {
        invite_token: inviteToken,
        seat: selectedSeat,
        kind: 'human',
        display_name: name.trim(),
      })
      storeSeatToken(roomId, res.seat_token)
      navigate(`/room/${roomId}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.body.reason : String(e))
    } finally {
      setLoading(false)
    }
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center text-red-400">{error}</div>
    )
  }

  if (!room) {
    return <div className="min-h-screen flex items-center justify-center text-neutral-500">Loading...</div>
  }

  const takenCount = room.seats.filter((s) => s.status === 'claimed').length

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-2xl border border-neutral-800 rounded-lg bg-neutral-950">
        <div className="p-6 border-b border-neutral-800">
          <div className="text-xs uppercase tracking-wider text-neutral-500">
            You've been invited to
          </div>
          <div className="text-2xl font-bold mt-1">{room.room_id}</div>
          <div className="flex gap-8 mt-4 text-sm">
            <div>
              <div className="text-xs uppercase tracking-wider text-neutral-500">Game</div>
              <div>{room.game}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-neutral-500">Status</div>
              <div>{room.status}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-neutral-500">Seats</div>
              <div>
                {takenCount} of {room.seats.length} taken
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 p-6">
          {room.seats.map((s) => {
            const isOpen = s.status === 'open'
            const isSelected = selectedSeat === s.index
            return (
              <button
                key={s.index}
                disabled={!isOpen}
                onClick={() => setSelectedSeat(s.index)}
                className={`text-left p-4 rounded-md border flex items-center justify-between ${
                  isSelected
                    ? 'border-cyan-500 bg-cyan-950/20'
                    : isOpen
                      ? 'border-dashed border-neutral-700'
                      : 'border-neutral-800'
                } ${!isOpen ? 'cursor-default' : 'cursor-pointer'}`}
              >
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-md border border-neutral-700 flex items-center justify-center text-sm">
                    {isOpen ? s.index + 1 : (s.name?.[0] ?? '?').toUpperCase()}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span>{isOpen ? `Seat ${s.index + 1}` : s.name}</span>
                      <span className="text-[10px] uppercase tracking-wider border border-neutral-700 rounded px-1 py-0.5 text-neutral-400">
                        {isOpen ? 'open' : s.kind}
                      </span>
                    </div>
                  </div>
                </div>
                {isSelected && (
                  <span className="text-[10px] uppercase tracking-wider bg-cyan-500 text-black rounded px-2 py-1">
                    Selected
                  </span>
                )}
              </button>
            )
          })}
        </div>

        <div className="flex gap-3 px-6 pb-6">
          <input
            className="flex-1 bg-neutral-900 border border-neutral-700 rounded-md px-3 py-2 outline-none focus:border-cyan-500"
            placeholder="your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            disabled={selectedSeat == null || !name.trim() || loading}
            onClick={handleTakeSeat}
            className="bg-cyan-400 disabled:bg-neutral-700 disabled:text-neutral-500 text-black font-medium rounded-md px-4 py-2"
          >
            {loading ? 'Joining...' : `Take seat ${selectedSeat != null ? selectedSeat + 1 : ''}`}
          </button>
        </div>

        <div className="px-6 py-4 border-t border-neutral-800 text-xs text-neutral-500">
          Empty seats can be filled with a model or agent by the host.
        </div>
      </div>
    </div>
  )
}
