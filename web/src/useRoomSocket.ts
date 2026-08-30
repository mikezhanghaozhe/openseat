import { useEffect, useRef, useState, useCallback } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { getWsTicket, wsUrl } from './api'
import { normalizeEvent } from './protocol'
import type { Action, EventBase, LegalAction, Observation, ServerFrame } from './protocol'

export interface WsErrorState {
  code: string
  reason: string
  legal_actions?: LegalAction[]
}

export function useRoomSocket(roomId: string, seatToken: string | null) {
  const [observation, setObservation] = useState<Observation | null>(null)
  const [events, setEvents] = useState<EventBase[]>([])
  const [clockDeadline, setClockDeadline] = useState<number | null>(null)
  const [wsError, setWsError] = useState<WsErrorState | null>(null)
  const [connected, setConnected] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const lastSeqRef = useRef<number>(-1)
  const seenSeqRef = useRef<Set<number>>(new Set())
  const closedByUsRef = useRef(false)
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const applyEvent = useCallback((ev: EventBase) => {
    if (seenSeqRef.current.has(ev.seq)) return
    seenSeqRef.current.add(ev.seq)
    lastSeqRef.current = Math.max(lastSeqRef.current, ev.seq)
    setEvents((prev) => [...prev, ev].sort((a, b) => a.seq - b.seq))
  }, [])

  const connect = useCallback(async () => {
    if (!seatToken) return
    // Ticket is single-use and expires in 30s — fetch fresh on every connect, including reconnects.
    const { ticket } = await getWsTicket(roomId, seatToken)
    const ws = new WebSocket(wsUrl(roomId, ticket))
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      if (lastSeqRef.current >= 0) {
        const resumeFrame = { t: 'resume', since: lastSeqRef.current }
        ws.send(JSON.stringify(resumeFrame))
      }
      pingIntervalRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ t: 'ping' }))
      }, 20000)
    }

    ws.onmessage = (msg) => {
      const frame = JSON.parse(msg.data) as ServerFrame
      switch (frame.t) {
        case 'hello':
          frame.replay.map(normalizeEvent).forEach(applyEvent)
          break
        case 'state':
          setObservation(frame.payload)
          break
        case 'event':
          applyEvent(normalizeEvent(frame.payload))
          break
        case 'clock':
          setClockDeadline(frame.deadline_ms)
          break
        case 'error':
          setWsError({ code: frame.code, reason: frame.reason, legal_actions: frame.legal_actions })
          break
        case 'pong':
          break
      }
    }

    ws.onclose = () => {
      setConnected(false)
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current)
      if (!closedByUsRef.current) {
        setTimeout(() => connect(), 1000)
      }
    }
  }, [roomId, seatToken, applyEvent])

  useEffect(() => {
    closedByUsRef.current = false
    connect()
    return () => {
      closedByUsRef.current = true
      wsRef.current?.close()
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, seatToken])

  const sendAction = useCallback((action: Action, tableTalk?: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    setWsError(null)
    ws.send(
      JSON.stringify({
        t: 'act',
        request_id: uuidv4(),
        action,
        ...(tableTalk ? { table_talk: tableTalk } : {}),
      }),
    )
  }, [])

  return { observation, events, clockDeadline, wsError, connected, sendAction }
}
