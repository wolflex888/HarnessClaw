import type { AgentConfig } from '../types'

interface Props {
  agents: AgentConfig[]
  activeAgentId: string | null
  onSelect: (id: string) => void
  onNewAgent: () => void
}

export function AgentSidebar({ agents, activeAgentId, onSelect, onNewAgent }: Props) {
  // Build parent → children map for orchestrators
  const childIds = new Set(agents.flatMap((a) => a.orchestrates))
  const topLevel = agents.filter((a) => !childIds.has(a.id))

  function renderAgent(agent: AgentConfig, indent = false) {
    const isActive = agent.id === activeAgentId
    const isOrchestrator = agent.orchestrates.length > 0
    const children = agents.filter((a) => agent.orchestrates.includes(a.id))

    return (
      <div key={agent.id}>
        <button
          onClick={() => onSelect(agent.id)}
          className={`w-full text-left px-3 py-2 rounded text-sm flex items-center gap-2 transition-colors ${
            indent ? 'ml-4 w-[calc(100%-1rem)]' : ''
          } ${
            isActive
              ? 'bg-blue-900/40 text-blue-300 border-l-2 border-blue-500'
              : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
          }`}
        >
          <span className="text-xs">{isOrchestrator ? '⬡' : '○'}</span>
          <span className="truncate">{agent.name}</span>
        </button>
        {children.map((child) => renderAgent(child, true))}
      </div>
    )
  }

  return (
    <div className="w-52 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full">
      <div className="px-3 py-3 border-b border-gray-800">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Agents
        </span>
      </div>
      <div className="flex-1 overflow-y-auto py-2 px-2 flex flex-col gap-1">
        {topLevel.map((agent) => renderAgent(agent))}
      </div>
      <div className="p-2 border-t border-gray-800">
        <button
          onClick={onNewAgent}
          className="w-full py-2 rounded text-sm text-white bg-green-800 hover:bg-green-700 transition-colors"
        >
          + New Agent
        </button>
      </div>
    </div>
  )
}
