import { useState } from 'react'
import type { RoleConfig } from '../types'

interface Props {
  roles: RoleConfig[]
  onCreate: (roleId: string, workingDir: string) => void
  onCancel: () => void
}

export function SessionCreatePanel({ roles, onCreate, onCancel }: Props) {
  const [roleId, setRoleId] = useState(roles.find(r => r.id === 'general-purpose')?.id ?? roles[0]?.id ?? '')
  const [workingDir, setWorkingDir] = useState('~/src')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (roleId && workingDir.trim()) {
      onCreate(roleId, workingDir.trim())
    }
  }

  return (
    <div className="flex-1 flex items-center justify-center bg-gray-950">
      <form onSubmit={handleSubmit} className="w-96 bg-gray-900 rounded-lg p-6 flex flex-col gap-4 border border-gray-800">
        <h2 className="text-white text-lg font-semibold">New Session</h2>

        <div className="flex flex-col gap-1">
          <label className="text-gray-400 text-sm">Directory</label>
          <input
            type="text"
            value={workingDir}
            onChange={(e) => setWorkingDir(e.target.value)}
            className="bg-gray-800 text-white text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:border-blue-500"
            placeholder="~/src/my-project"
          />
          <span className="text-gray-600 text-xs">Path within ~/src</span>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-gray-400 text-sm">Role</label>
          <select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            className="bg-gray-800 text-white text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:border-blue-500"
          >
            {roles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            Create
          </button>
        </div>
      </form>
    </div>
  )
}
