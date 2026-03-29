import { useEffect, useRef, useState, useCallback } from 'react'
import { WsClient } from './ws'
import type { RoleConfig, SessionState, Job, WSIncoming } from './types'
import { SessionSidebar } from './components/SessionSidebar'
import { SessionCreatePanel } from './components/SessionCreatePanel'
import { SessionCostBar } from './components/SessionCostBar'
import { TabPanel } from './components/TabPanel'
import { WorkTab } from './components/WorkTab'
import { TasksTab } from './components/TasksTab'
import { AgentTab } from './components/AgentTab'
import { ToolsTab } from './components/ToolsTab'

function emptySessionState(data: { session_id: string; role_id: string; working_dir: string; model: string; name: string; status: 'idle' | 'running' | 'killed' }): SessionState {
  return {
    ...data,
    messages: [],
    streamingMessages: {},
    jobs: [],
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    tools: [],
    pendingPermissions: [],
  }
}

let msgCounter = 0
function nextId() { return String(++msgCounter) }

export default function App() {
  const [roles, setRoles] = useState<RoleConfig[]>([])
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const wsRef = useRef<WsClient | null>(null)
  const jobSessionMap = useRef<Record<string, string>>({})  // job_id → session_id

  // Load roles + sessions from REST
  useEffect(() => {
    fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
    fetch('/api/sessions').then(r => r.json()).then((grouped: Record<string, Array<{
      session_id: string; role_id: string; working_dir: string; model: string;
      name: string; status: 'idle' | 'running' | 'killed'; claude_session_id: string | null;
      input_tokens: number; output_tokens: number;
    }>>) => {
      const flat: Record<string, SessionState> = {}
      for (const sessionList of Object.values(grouped)) {
        for (const s of sessionList) {
          flat[s.session_id] = emptySessionState(s)
          flat[s.session_id].input_tokens = s.input_tokens
          flat[s.session_id].output_tokens = s.output_tokens
        }
      }
      setSessions(flat)
      const first = Object.values(flat)[0]
      if (first) setActiveSessionId(first.session_id)
    }).catch(console.error)
  }, [])

  const handleWsMessage = useCallback((raw: unknown) => {
    const msg = raw as WSIncoming

    if (msg.type === 'job_update') {
      const sessionId = msg.session_id
      jobSessionMap.current[msg.job_id] = sessionId
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        const existingJob = existing.jobs.find(j => j.job_id === msg.job_id)
        let updatedJobs: Job[]
        if (!existingJob) {
          updatedJobs = [...existing.jobs, { job_id: msg.job_id, session_id: sessionId, title: msg.title ?? '', status: msg.status, progress: msg.progress }]
        } else {
          updatedJobs = existing.jobs.map(j => j.job_id === msg.job_id ? { ...j, status: msg.status, progress: msg.progress } : j)
        }
        let updatedMessages = existing.messages
        let updatedStreaming = existing.streamingMessages
        if (msg.status === 'completed' && existing.streamingMessages[msg.job_id]) {
          const text = existing.streamingMessages[msg.job_id]
          updatedMessages = [...existing.messages, { id: nextId(), role: 'assistant' as const, content: text }]
          const { [msg.job_id]: _, ...rest } = existing.streamingMessages
          updatedStreaming = rest
        }
        return { ...prev, [sessionId]: { ...existing, jobs: updatedJobs, messages: updatedMessages, streamingMessages: updatedStreaming } }
      })
    } else if (msg.type === 'token') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, streamingMessages: { ...existing.streamingMessages, [msg.job_id]: (existing.streamingMessages[msg.job_id] ?? '') + msg.delta } } }
      })
    } else if (msg.type === 'usage') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, input_tokens: existing.input_tokens + msg.input_tokens, output_tokens: existing.output_tokens + msg.output_tokens, cost_usd: existing.cost_usd + msg.cost_usd } }
      })
    } else if (msg.type === 'error') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'assistant' as const, content: `⚠ Error: ${msg.message}` }] } }
      })
    } else if (msg.type === 'permission_request') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return { ...prev, [msg.session_id]: { ...existing, pendingPermissions: [...existing.pendingPermissions, { request_id: msg.request_id, tool_name: msg.tool_name, input: msg.input }] } }
      })
    } else if (msg.type === 'session_update') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return { ...prev, [msg.session_id]: { ...existing, name: msg.name, status: msg.status } }
      })
    } else if (msg.type === 'session_deleted') {
      setSessions(prev => {
        const next = { ...prev }
        delete next[msg.session_id]
        return next
      })
      setActiveSessionId(prev => prev === msg.session_id ? null : prev)
    } else if (msg.type === 'tool_call') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'assistant' as const, content: `→ Calling: ${msg.tool_name}`, tool_calls: [{ tool_id: msg.job_id, name: msg.tool_name, input: msg.input }] }] } }
      })
    }
  }, [])

  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  const handleSend = useCallback((text: string) => {
    if (!activeSessionId) return
    setSessions(prev => {
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'user', content: text }] } }
    })
    wsRef.current?.send({ type: 'chat', session_id: activeSessionId, text })
  }, [activeSessionId])

  const handleAllow = useCallback((requestId: string) => {
    wsRef.current?.send({ type: 'permission_response', request_id: requestId, approved: true })
    setSessions(prev => {
      if (!activeSessionId) return prev
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, pendingPermissions: existing.pendingPermissions.filter(p => p.request_id !== requestId) } }
    })
  }, [activeSessionId])

  const handleDeny = useCallback((requestId: string) => {
    wsRef.current?.send({ type: 'permission_response', request_id: requestId, approved: false })
    setSessions(prev => {
      if (!activeSessionId) return prev
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, pendingPermissions: existing.pendingPermissions.filter(p => p.request_id !== requestId) } }
    })
  }, [activeSessionId])

  const handleKill = useCallback((sessionId: string) => {
    wsRef.current?.send({ type: 'cancel', session_id: sessionId })
  }, [])

  const handleDelete = useCallback(async (sessionId: string) => {
    await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
  }, [])

  const handleCreateSession = useCallback(async (roleId: string, workingDir: string) => {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role_id: roleId, working_dir: workingDir }),
    })
    if (!res.ok) return
    const data = await res.json()
    setSessions(prev => ({ ...prev, [data.session_id]: emptySessionState(data) }))
    setActiveSessionId(data.session_id)
    setShowCreate(false)
  }, [])

  const activeSession = activeSessionId ? sessions[activeSessionId] : null
  const activeRole = roles.find(r => r.id === activeSession?.role_id)

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950 text-gray-200">
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={(id) => { setActiveSessionId(id); setShowCreate(false) }}
        onNewSession={() => setShowCreate(true)}
        onDelete={handleDelete}
        onKill={handleKill}
      />

      <div className="flex flex-col flex-1 min-w-0">
        {showCreate ? (
          <SessionCreatePanel
            roles={roles}
            onCreate={handleCreateSession}
            onCancel={() => setShowCreate(false)}
          />
        ) : activeSession ? (
          <>
            <SessionCostBar
              model={activeSession.model}
              inputTokens={activeSession.input_tokens}
              outputTokens={activeSession.output_tokens}
              costUsd={activeSession.cost_usd}
              sessionName={activeSession.name}
              status={activeSession.status}
            />
            <TabPanel>
              {(activeTab) => {
                if (activeTab === 'work') return (
                  <WorkTab
                    messages={activeSession.messages}
                    streamingMessages={activeSession.streamingMessages}
                    pendingPermissions={activeSession.pendingPermissions}
                    onSend={handleSend}
                    onAllow={handleAllow}
                    onDeny={handleDeny}
                  />
                )
                if (activeTab === 'tasks') return <TasksTab jobs={activeSession.jobs} />
                if (activeTab === 'agent') return <AgentTab session={activeSession} role={activeRole} />
                if (activeTab === 'tools') return <ToolsTab tools={activeSession.tools} />
                return null
              }}
            </TabPanel>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Select a session or create a new one
          </div>
        )}
      </div>
    </div>
  )
}
