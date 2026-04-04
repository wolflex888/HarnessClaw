import type { RoleConfig, SessionState, AgentStatus, ToolEvent } from '../types'

interface Props {
  session: SessionState
  role: RoleConfig | undefined
  agentStatus: AgentStatus | null
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function ActivityTimeline({ events }: { events: ToolEvent[] }) {
  if (events.length === 0) {
    return <span className="text-gray-600 text-xs">No activity yet</span>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {events.map((ev, i) => (
        <div key={i} className="flex items-center gap-2 text-xs font-mono">
          <span className="text-gray-300 w-24 truncate">{ev.tool_name}</span>
          <span className="text-gray-500 w-12 text-right">{formatDuration(ev.duration_ms)}</span>
          <span className="text-gray-600">{formatTime(ev.started_at)}</span>
        </div>
      ))}
    </div>
  )
}

export function AgentTab({ session, role, agentStatus }: Props) {
  return (
    <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Role</span>
        <span className="text-white">{role?.name ?? session.role_id}</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Model</span>
        <span className="text-gray-300">{session.model}</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Working Directory</span>
        <span className="text-gray-300 font-mono text-xs">{session.working_dir}</span>
      </div>
      {role?.system_prompt && (
        <div className="flex flex-col gap-1">
          <span className="text-gray-500 text-xs uppercase tracking-wide">System Prompt</span>
          <pre className="text-gray-300 text-xs bg-gray-800 rounded p-3 whitespace-pre-wrap border border-gray-700">
            {role.system_prompt}
          </pre>
        </div>
      )}
      <div className="flex flex-col gap-2">
        <span className="text-gray-500 text-xs uppercase tracking-wide">
          Activity
          {agentStatus?.is_stuck && (
            <span className="ml-2 text-amber-400 normal-case">
              ⚠ {agentStatus.stuck_reason?.replace('_', ' ')}
            </span>
          )}
        </span>
        {agentStatus ? (
          <ActivityTimeline events={agentStatus.recent_activity} />
        ) : (
          <span className="text-gray-600 text-xs">Observability not available</span>
        )}
      </div>
    </div>
  )
}
