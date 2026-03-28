import { useEffect, useRef, useState, useCallback } from 'react'
import { WsClient } from './ws'
import type { AgentConfig, Job, SessionState, WSIncoming } from './types'
import { AgentSidebar } from './components/AgentSidebar'
import { ChatPanel } from './components/ChatPanel'
import { JobsPanel } from './components/JobsPanel'
import { SessionCostBar } from './components/SessionCostBar'
import { AgentConfigPanel } from './components/AgentConfigPanel'

function emptySession(model: string): SessionState {
  return {
    messages: [],
    streamingMessages: {},
    jobs: [],
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    model,
  }
}

let msgCounter = 0
function nextId() {
  return String(++msgCounter)
}

export default function App() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [activeAgentId, setActiveAgentId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [showConfig, setShowConfig] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentConfig | null>(null)
  const wsRef = useRef<WsClient | null>(null)
  // Map job_id → agent_id for routing incoming WebSocket messages
  const jobAgentMap = useRef<Record<string, string>>({})

  // Load agents from REST API
  useEffect(() => {
    fetch('/api/agents')
      .then((r) => r.json())
      .then((data: AgentConfig[]) => {
        setAgents(data)
        if (data.length > 0) setActiveAgentId(data[0].id)
      })
      .catch(console.error)
  }, [])

  const handleWsMessage = useCallback((raw: unknown) => {
    const msg = raw as WSIncoming

    if (msg.type === 'job_update') {
      const agentId = msg.agent_id
      jobAgentMap.current[msg.job_id] = agentId

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        const existingJob = existing.jobs.find((j) => j.job_id === msg.job_id)

        let updatedJobs: Job[]
        if (!existingJob) {
          updatedJobs = [
            ...existing.jobs,
            {
              job_id: msg.job_id,
              agent_id: agentId,
              title: msg.title ?? '',
              status: msg.status,
              progress: msg.progress,
            },
          ]
        } else {
          updatedJobs = existing.jobs.map((j) =>
            j.job_id === msg.job_id ? { ...j, status: msg.status, progress: msg.progress } : j,
          )
        }

        // When a job completes, move its streaming text to messages
        let updatedMessages = existing.messages
        let updatedStreaming = existing.streamingMessages
        if (msg.status === 'completed' && existing.streamingMessages[msg.job_id]) {
          const text = existing.streamingMessages[msg.job_id]
          updatedMessages = [
            ...existing.messages,
            { id: nextId(), role: 'assistant' as const, content: text },
          ]
          const { [msg.job_id]: _, ...rest } = existing.streamingMessages
          updatedStreaming = rest
        }

        return {
          ...prev,
          [agentId]: {
            ...existing,
            messages: updatedMessages,
            streamingMessages: updatedStreaming,
            jobs: updatedJobs,
          },
        }
      })
    } else if (msg.type === 'token') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            streamingMessages: {
              ...existing.streamingMessages,
              [msg.job_id]: (existing.streamingMessages[msg.job_id] ?? '') + msg.delta,
            },
          },
        }
      })
    } else if (msg.type === 'usage') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            input_tokens: msg.input_tokens,
            output_tokens: msg.output_tokens,
            cost_usd: msg.cost_usd,
          },
        }
      })
    } else if (msg.type === 'error') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            messages: [
              ...existing.messages,
              {
                id: nextId(),
                role: 'assistant' as const,
                content: `⚠ Error: ${msg.message}`,
              },
            ],
          },
        }
      })
    }
  }, [])

  // Connect WebSocket
  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  function handleSend(text: string) {
    if (!activeAgentId) return

    // Optimistically add user message
    setSessions((prev) => {
      const existing = prev[activeAgentId] ?? emptySession('')
      return {
        ...prev,
        [activeAgentId]: {
          ...existing,
          messages: [...existing.messages, { id: nextId(), role: 'user', content: text }],
        },
      }
    })

    wsRef.current?.send({ type: 'chat', agent_id: activeAgentId, text })
  }

  async function handleSaveAgent(config: AgentConfig) {
    const isEdit = agents.some((a) => a.id === config.id)
    const url = isEdit ? `/api/agents/${config.id}` : '/api/agents'
    const method = isEdit ? 'PUT' : 'POST'

    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!res.ok) return

    const saved: AgentConfig = await res.json()
    setAgents((prev) =>
      isEdit ? prev.map((a) => (a.id === saved.id ? saved : a)) : [...prev, saved],
    )
    setShowConfig(false)
    setEditingAgent(null)
    setActiveAgentId(saved.id)
  }

  const activeSession = activeAgentId ? (sessions[activeAgentId] ?? emptySession('')) : null
  const activeAgent = agents.find((a) => a.id === activeAgentId)

  return (
    <div className="flex h-screen overflow-hidden">
      <AgentSidebar
        agents={agents}
        activeAgentId={activeAgentId}
        onSelect={(id) => {
          setActiveAgentId(id)
          setShowConfig(false)
          setEditingAgent(null)
        }}
        onNewAgent={() => {
          setShowConfig(true)
          setEditingAgent(null)
        }}
      />

      <div className="flex flex-col flex-1 min-w-0">
        {showConfig || editingAgent ? (
          <AgentConfigPanel
            agents={agents}
            editingAgent={editingAgent}
            onSave={handleSaveAgent}
            onCancel={() => {
              setShowConfig(false)
              setEditingAgent(null)
            }}
          />
        ) : activeAgent && activeSession ? (
          <>
            <SessionCostBar
              model={activeAgent.model}
              inputTokens={activeSession.input_tokens}
              outputTokens={activeSession.output_tokens}
              costUsd={activeSession.cost_usd}
            />
            <div className="flex flex-1 min-h-0">
              <ChatPanel
                messages={activeSession.messages}
                streamingMessages={activeSession.streamingMessages}
                onSend={handleSend}
              />
              <JobsPanel jobs={activeSession.jobs} />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Select an agent to start chatting
          </div>
        )}
      </div>
    </div>
  )
}
