import { useState, useEffect, useCallback } from 'react'

interface AuditEvent {
  event_id: string
  timestamp: string
  subject: string
  operation: string
  resource: string
  outcome: 'allowed' | 'denied' | 'error'
  details: Record<string, unknown>
}

const OUTCOME_STYLE: Record<string, string> = {
  allowed: 'text-green-400',
  denied: 'text-red-400',
  error: 'text-yellow-400',
}

export function AuditTab() {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const [filter, setFilter] = useState('')

  const load = useCallback(() => {
    fetch('/api/audit?limit=200')
      .then(r => r.json())
      .then(setEvents)
      .catch(console.error)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const visible = filter
    ? events.filter(e =>
        e.subject.includes(filter) ||
        e.operation.includes(filter) ||
        e.outcome.includes(filter) ||
        e.resource.includes(filter)
      )
    : events

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Event list */}
      <div className="flex flex-col flex-1 min-w-0 border-r border-gray-800">
        <div className="flex items-center gap-2 p-2 border-b border-gray-800">
          <input
            type="text"
            placeholder="Filter by subject, operation, outcome..."
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="flex-1 bg-gray-800 text-sm text-gray-200 rounded px-2 py-1 outline-none placeholder-gray-600"
          />
          <button
            onClick={load}
            className="text-xs text-gray-400 hover:text-gray-200 px-2 py-1 border border-gray-700 rounded"
          >
            Refresh
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {visible.length === 0 && (
            <div className="flex items-center justify-center h-full text-gray-600 text-sm">
              No audit events yet
            </div>
          )}
          {visible.map(ev => (
            <button
              key={ev.event_id}
              onClick={() => setSelected(ev)}
              className={`w-full px-3 py-2 text-left border-b border-gray-800 hover:bg-gray-800 flex items-center gap-3 ${
                selected?.event_id === ev.event_id ? 'bg-gray-800' : ''
              }`}
            >
              <span className={`text-xs font-mono w-14 shrink-0 ${OUTCOME_STYLE[ev.outcome] ?? 'text-gray-400'}`}>
                {ev.outcome}
              </span>
              <span className="text-xs text-blue-400 font-mono w-32 shrink-0 truncate">
                {ev.operation}
              </span>
              <span className="text-xs text-gray-400 truncate flex-1">
                {ev.subject}
              </span>
              <span className="text-xs text-gray-600 shrink-0">
                {new Date(ev.timestamp).toLocaleTimeString()}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Detail panel */}
      <div className="w-80 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="p-3 border-b border-gray-800">
              <div className={`text-xs font-semibold mb-1 ${OUTCOME_STYLE[selected.outcome] ?? 'text-gray-400'}`}>
                {selected.outcome.toUpperCase()}
              </div>
              <div className="text-sm text-white font-mono">{selected.operation}</div>
              {selected.resource && (
                <div className="text-xs text-gray-500 mt-0.5 truncate">{selected.resource}</div>
              )}
            </div>
            <div className="p-3 flex flex-col gap-2 overflow-y-auto flex-1">
              <div>
                <div className="text-xs text-gray-500 mb-0.5">Subject</div>
                <div className="text-xs text-gray-300 font-mono break-all">{selected.subject}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500 mb-0.5">Timestamp</div>
                <div className="text-xs text-gray-300">{new Date(selected.timestamp).toLocaleString()}</div>
              </div>
              {Object.keys(selected.details).length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">Details</div>
                  <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap bg-gray-800 rounded p-2">
                    {JSON.stringify(selected.details, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Select an event
          </div>
        )}
      </div>
    </div>
  )
}
