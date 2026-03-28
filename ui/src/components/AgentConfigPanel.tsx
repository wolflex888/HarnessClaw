import { useState } from 'react'
import type { AgentConfig } from '../types'

interface Props {
  agents: AgentConfig[]
  editingAgent?: AgentConfig | null
  onSave: (config: AgentConfig) => void
  onCancel: () => void
}

export function AgentConfigPanel({ agents, editingAgent, onSave, onCancel }: Props) {
  const [id, setId] = useState(editingAgent?.id ?? '')
  const [name, setName] = useState(editingAgent?.name ?? '')
  const [model, setModel] = useState(editingAgent?.model ?? 'claude-sonnet-4-6')
  const [systemPrompt, setSystemPrompt] = useState(editingAgent?.system_prompt ?? 'You are a helpful assistant.')
  const [maxTokens, setMaxTokens] = useState(editingAgent?.max_tokens ?? 4096)
  const [orchestrates, setOrchestrates] = useState<string[]>(editingAgent?.orchestrates ?? [])

  const models = [
    'claude-sonnet-4-6',
    'claude-opus-4-6',
    'claude-haiku-4-5-20251001',
  ]

  const potentialSubAgents = agents.filter((a) => a.id !== id)

  function toggleSubAgent(agentId: string) {
    setOrchestrates((prev) =>
      prev.includes(agentId) ? prev.filter((x) => x !== agentId) : [...prev, agentId],
    )
  }

  function handleSave() {
    if (!id.trim() || !name.trim()) return
    onSave({
      id: id.trim(),
      name: name.trim(),
      provider: 'anthropic',
      model,
      system_prompt: systemPrompt,
      max_tokens: maxTokens,
      orchestrates,
    })
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl">
        <h2 className="text-lg font-semibold text-gray-200 mb-6">
          {editingAgent ? `Edit: ${editingAgent.name}` : 'New Agent'}
        </h2>

        <div className="flex flex-col gap-4">
          {/* ID */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Agent ID
            </label>
            <input
              value={id}
              onChange={(e) => setId(e.target.value)}
              disabled={!!editingAgent}
              placeholder="my-agent"
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-600 disabled:opacity-50"
            />
            <p className="mt-1 text-xs text-gray-600">Lowercase, hyphen-separated. Cannot be changed after creation.</p>
          </div>

          {/* Name */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Display Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Agent"
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-600"
            />
          </div>

          {/* Model */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-600"
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>

          {/* System Prompt */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              System Prompt
            </label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-y focus:outline-none focus:border-blue-600 font-mono"
            />
          </div>

          {/* Max Tokens */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Max Tokens
            </label>
            <input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
              min={256}
              max={65536}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          {/* Sub-agents (orchestrates) */}
          {potentialSubAgents.length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                Orchestrates (sub-agents)
              </label>
              <div className="flex flex-col gap-1">
                {potentialSubAgents.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={orchestrates.includes(a.id)}
                      onChange={() => toggleSubAgent(a.id)}
                      className="accent-blue-500"
                    />
                    <span className="text-sm text-gray-300">{a.name}</span>
                    <span className="text-xs text-gray-600">({a.id})</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleSave}
              className="px-4 py-2 bg-green-800 hover:bg-green-700 text-white text-sm rounded-md transition-colors"
            >
              {editingAgent ? 'Save Changes' : 'Create Agent'}
            </button>
            <button
              onClick={onCancel}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-md transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
