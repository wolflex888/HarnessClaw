import type { PendingPermission } from '../types'

interface Props {
  permission: PendingPermission
  onAllow: (requestId: string) => void
  onDeny: (requestId: string) => void
}

export function PermissionDialog({ permission, onAllow, onDeny }: Props) {
  const inputStr = Object.entries(permission.input)
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('\n')

  return (
    <div className="mx-4 my-2 bg-gray-800 border border-yellow-600 rounded-lg p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-yellow-500 text-sm">🔧</span>
        <span className="text-yellow-400 text-sm font-medium">{permission.tool_name}</span>
      </div>
      {inputStr && (
        <pre className="text-gray-300 text-xs bg-gray-900 rounded p-2 overflow-x-auto whitespace-pre-wrap">
          {inputStr}
        </pre>
      )}
      <div className="flex gap-2 justify-end">
        <button
          onClick={() => onDeny(permission.request_id)}
          className="px-3 py-1 text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 rounded"
        >
          Deny
        </button>
        <button
          onClick={() => onAllow(permission.request_id)}
          className="px-3 py-1 text-xs bg-green-700 hover:bg-green-600 text-white rounded"
        >
          Allow
        </button>
      </div>
    </div>
  )
}
