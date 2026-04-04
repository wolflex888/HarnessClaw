import { useEffect, useRef, useState } from 'react'
import type { SessionState, AgentStatus } from '../types'

interface Props {
  sessions: Record<string, SessionState>
  agentStatuses: Record<string, AgentStatus>
  activeSessionId: string | null
  onSelect: (sessionId: string) => void
  onNewSession: () => void
  onDelete: (sessionId: string) => void
  onKill: (sessionId: string) => void
}

function statusDot(status: string): string {
  if (status === 'running') return '●'
  if (status === 'killed') return '✕'
  return '○'
}

function statusColor(status: string): string {
  if (status === 'running') return 'text-blue-400'
  if (status === 'killed') return 'text-red-400'
  return 'text-gray-500'
}

function useElapsedSeconds(startedAt: string | null): number {
  const [elapsed, setElapsed] = useState(0)
  const startedAtRef = useRef(startedAt)
  startedAtRef.current = startedAt

  useEffect(() => {
    if (!startedAt) { setElapsed(0); return }
    const tick = () => {
      const diff = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
      setElapsed(Math.max(0, diff))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [startedAt])

  return elapsed
}

function ToolBadge({ status }: { status: AgentStatus }) {
  const elapsed = useElapsedSeconds(status.current_tool_started_at)
  if (!status.current_tool) return null
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded font-mono ml-1 ${
        status.is_stuck ? 'bg-amber-900 text-amber-300' : 'bg-gray-700 text-gray-300'
      }`}
      title={status.stuck_reason ?? undefined}
    >
      {status.current_tool} {elapsed}s
      {status.is_stuck && ' ⚠'}
    </span>
  )
}

export function SessionSidebar({
  sessions, agentStatuses, activeSessionId, onSelect, onNewSession, onDelete, onKill,
}: Props) {
  const grouped: Record<string, SessionState[]> = {}
  for (const s of Object.values(sessions)) {
    if (!grouped[s.working_dir]) grouped[s.working_dir] = []
    grouped[s.working_dir].push(s)
  }

  return (
    <div className="w-56 flex-shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full">
      <div className="flex-1 overflow-y-auto py-2">
        {Object.entries(grouped).map(([dir, dirSessions]) => (
          <div key={dir} className="mb-3">
            <div className="px-3 py-1 text-xs text-gray-500 font-medium truncate" title={dir}>
              {dir.replace(/^~\/src\//, '')}
            </div>
            {dirSessions.map((s) => {
              const agentStatus = agentStatuses[s.session_id] ?? null
              return (
                <div
                  key={s.session_id}
                  className={`group flex items-center px-3 py-2 cursor-pointer text-sm hover:bg-gray-800 ${
                    s.session_id === activeSessionId ? 'bg-gray-800 text-white' : 'text-gray-400'
                  }`}
                  onClick={() => onSelect(s.session_id)}
                >
                  <span className={`mr-1.5 text-xs ${statusColor(s.status)}`}>{statusDot(s.status)}</span>
                  <span className="flex-1 truncate min-w-0">{s.name || 'New session'}</span>
                  {agentStatus && <ToolBadge status={agentStatus} />}
                  {s.status === 'running' && (
                    <button
                      className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs flex-shrink-0"
                      onClick={(e) => { e.stopPropagation(); onKill(s.session_id) }}
                      title="Kill"
                    >■</button>
                  )}
                  {s.status !== 'running' && (
                    <button
                      className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs flex-shrink-0"
                      onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
                      title="Delete"
                    >✕</button>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <div className="p-3 border-t border-gray-800">
        <button
          onClick={onNewSession}
          className="w-full text-left text-sm text-gray-400 hover:text-white py-1"
        >
          + New Session
        </button>
      </div>
    </div>
  )
}
