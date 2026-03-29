import type { ToolInfo } from '../types'

interface Props {
  tools: ToolInfo[]
}

export function ToolsTab({ tools }: Props) {
  if (tools.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        Tools will appear once a session starts
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2">
      {tools.map((tool) => (
        <div key={tool.name} className="bg-gray-800 rounded-lg p-3 border border-gray-700 flex flex-col gap-1">
          <span className="text-sm text-white font-medium">{tool.name}</span>
          {tool.description && (
            <span className="text-xs text-gray-400">{tool.description}</span>
          )}
        </div>
      ))}
    </div>
  )
}
