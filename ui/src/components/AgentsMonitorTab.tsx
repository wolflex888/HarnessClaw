import type { SessionState, AgentStatus, TaskRecord } from '../types'

interface Props {
  sessions: Record<string, SessionState>
  agentStatuses: Record<string, AgentStatus>
  tasks: TaskRecord[]
  activeSessionId: string | null
  onSelectSession: (id: string) => void
  onKill: (id: string) => void
}

function statusBadge(status: string) {
  const colors: Record<string, string> = {
    running: 'bg-green-500/20 text-green-400',
    idle: 'bg-gray-500/20 text-gray-400',
    killed: 'bg-red-500/20 text-red-400',
    paused: 'bg-amber-500/20 text-amber-400',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[status] ?? colors.idle}`}>
      {status}
    </span>
  )
}

function stuckBadge(status: AgentStatus) {
  if (!status.is_stuck) return null
  return (
    <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-500/20 text-amber-400">
      ⚠ {status.stuck_reason?.replace(/_/g, ' ')}
    </span>
  )
}

function timeSince(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 1000) return 'just now'
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`
  return `${Math.floor(ms / 3_600_000)}h ago`
}

function formatCost(usd: number): string {
  if (usd === 0) return '—'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function AgentsMonitorTab({ sessions, agentStatuses, tasks, activeSessionId, onSelectSession, onKill }: Props) {
  const sessionList = Object.values(sessions).sort((a, b) => {
    // running first, then idle, then killed
    const order: Record<string, number> = { running: 0, idle: 1, killed: 2 }
    return (order[a.status] ?? 1) - (order[b.status] ?? 1)
  })

  // Count tasks per session
  const taskCounts: Record<string, { total: number; active: number }> = {}
  for (const t of tasks) {
    const sid = t.delegated_to
    if (!taskCounts[sid]) taskCounts[sid] = { total: 0, active: 0 }
    taskCounts[sid].total++
    if (t.status === 'running' || t.status === 'queued') taskCounts[sid].active++
  }

  const runningCount = sessionList.filter(s => s.status === 'running').length
  const totalCost = sessionList.reduce((sum, s) => sum + s.cost_usd, 0)

  return (
    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
      {/* Summary bar */}
      <div className="flex gap-6 text-xs text-gray-400 border-b border-gray-800 pb-3">
        <span>{sessionList.length} agents</span>
        <span className="text-green-400">{runningCount} running</span>
        <span>Total cost: {formatCost(totalCost)}</span>
        <span>
          {Object.values(agentStatuses).filter(s => s.is_stuck).length > 0 && (
            <span className="text-amber-400">
              ⚠ {Object.values(agentStatuses).filter(s => s.is_stuck).length} stuck
            </span>
          )}
        </span>
      </div>

      {/* Agent table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 uppercase tracking-wide border-b border-gray-800">
              <th className="text-left py-2 px-3">Agent</th>
              <th className="text-left py-2 px-3">Role</th>
              <th className="text-left py-2 px-3">Status</th>
              <th className="text-left py-2 px-3">Current Tool</th>
              <th className="text-left py-2 px-3">Last Activity</th>
              <th className="text-right py-2 px-3">Tasks</th>
              <th className="text-right py-2 px-3">Tokens</th>
              <th className="text-right py-2 px-3">Cost</th>
              <th className="text-right py-2 px-3"></th>
            </tr>
          </thead>
          <tbody>
            {sessionList.map(session => {
              const status = agentStatuses[session.session_id]
              const tc = taskCounts[session.session_id]
              const isActive = session.session_id === activeSessionId
              return (
                <tr
                  key={session.session_id}
                  onClick={() => onSelectSession(session.session_id)}
                  className={`border-b border-gray-800/50 cursor-pointer transition-colors ${
                    isActive ? 'bg-blue-500/10' : 'hover:bg-gray-800/50'
                  }`}
                >
                  <td className="py-2 px-3">
                    <div className="flex flex-col">
                      <span className="text-gray-200 font-medium truncate max-w-[200px]">
                        {session.name || session.session_id.slice(0, 8)}
                      </span>
                      <span className="text-gray-600 text-xs font-mono">{session.session_id.slice(0, 8)}</span>
                    </div>
                  </td>
                  <td className="py-2 px-3 text-gray-400">{session.role_id}</td>
                  <td className="py-2 px-3">
                    <div className="flex items-center gap-2">
                      {statusBadge(session.status)}
                      {status && stuckBadge(status)}
                    </div>
                  </td>
                  <td className="py-2 px-3">
                    {status?.current_tool ? (
                      <span className="text-blue-400 font-mono text-xs">{status.current_tool}</span>
                    ) : (
                      <span className="text-gray-600">—</span>
                    )}
                  </td>
                  <td className="py-2 px-3 text-gray-500 text-xs">
                    {timeSince(status?.last_output_at ?? null)}
                  </td>
                  <td className="py-2 px-3 text-right">
                    {tc ? (
                      <span className="text-gray-400">
                        {tc.active > 0 && <span className="text-green-400">{tc.active} active / </span>}
                        {tc.total}
                      </span>
                    ) : (
                      <span className="text-gray-600">—</span>
                    )}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-500 text-xs font-mono">
                    {session.input_tokens + session.output_tokens > 0
                      ? `${((session.input_tokens + session.output_tokens) / 1000).toFixed(0)}k`
                      : '—'}
                  </td>
                  <td className="py-2 px-3 text-right text-gray-400 text-xs">
                    {formatCost(session.cost_usd)}
                  </td>
                  <td className="py-2 px-3 text-right">
                    {session.status === 'running' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onKill(session.session_id) }}
                        className="text-red-500 hover:text-red-400 text-xs px-2 py-1 rounded hover:bg-red-500/10 transition-colors"
                      >
                        Kill
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
            {sessionList.length === 0 && (
              <tr>
                <td colSpan={9} className="py-8 text-center text-gray-600">
                  No agents running. Create a session from the sidebar.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
