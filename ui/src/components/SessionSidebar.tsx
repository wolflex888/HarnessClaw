import type { SessionState } from '../types'

interface Props {
  sessions: Record<string, SessionState>
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

export function SessionSidebar({ sessions, activeSessionId, onSelect, onNewSession, onDelete, onKill }: Props) {
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
            {dirSessions.map((s) => (
              <div
                key={s.session_id}
                className={`group flex items-center px-3 py-2 cursor-pointer text-sm hover:bg-gray-800 ${
                  s.session_id === activeSessionId ? 'bg-gray-800 text-white' : 'text-gray-400'
                }`}
                onClick={() => onSelect(s.session_id)}
              >
                <span className={`mr-1.5 text-xs ${statusColor(s.status)}`}>{statusDot(s.status)}</span>
                <span className="flex-1 truncate">{s.name || 'New session'}</span>
                {s.status === 'running' && (
                  <button
                    className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs"
                    onClick={(e) => { e.stopPropagation(); onKill(s.session_id) }}
                    title="Kill"
                  >■</button>
                )}
                {s.status !== 'running' && (
                  <button
                    className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs"
                    onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
                    title="Delete"
                  >✕</button>
                )}
              </div>
            ))}
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
