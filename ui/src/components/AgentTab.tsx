import type { RoleConfig, SessionState } from '../types'

interface Props {
  session: SessionState
  role: RoleConfig | undefined
}

export function AgentTab({ session, role }: Props) {
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
    </div>
  )
}
