import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react'
import { WsClient } from './ws'
import type { RoleConfig, SessionState, WSIncoming } from './types'
import { SessionSidebar } from './components/SessionSidebar'
import { SessionCreatePanel } from './components/SessionCreatePanel'
import { SessionCostBar } from './components/SessionCostBar'
import { TabPanel, type TabId } from './components/TabPanel'
import { TerminalTab } from './components/TerminalTab'
import { TasksTab } from './components/TasksTab'
import { AgentTab } from './components/AgentTab'
import { ToolsTab } from './components/ToolsTab'

function emptySessionState(data: {
  session_id: string; role_id: string; working_dir: string; model: string;
  name: string; status: 'idle' | 'running' | 'killed';
  input_tokens: number; output_tokens: number;
}): SessionState {
  return {
    ...data,
    cost_usd: 0,
    tools: [],
  }
}

export default function App() {
  const [roles, setRoles] = useState<RoleConfig[]>([])
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [activeTab, setActiveTab] = useState<TabId>('work')
  const wsRef = useRef<WsClient | null>(null)
  // Maps session_id → the xterm write function for that terminal
  const terminalWriters = useRef<Record<string, (data: Uint8Array) => void>>({})

  // Load roles + sessions from REST
  useEffect(() => {
    fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
    fetch('/api/sessions').then(r => r.json()).then((grouped: Record<string, Array<{
      session_id: string; role_id: string; working_dir: string; model: string;
      name: string; status: 'idle' | 'running' | 'killed';
      claude_session_id: string | null; input_tokens: number; output_tokens: number;
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

    if (msg.type === 'output') {
      const writeFn = terminalWriters.current[msg.session_id]
      if (writeFn) {
        const binary = atob(msg.data)
        const bytes = Uint8Array.from(binary, c => c.charCodeAt(0))
        writeFn(bytes)
      }
    } else if (msg.type === 'cost_update') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return {
          ...prev,
          [msg.session_id]: {
            ...existing,
            cost_usd: msg.cost_usd,
            input_tokens: msg.input_tokens,
            output_tokens: msg.output_tokens,
          },
        }
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
    }
  }, [])

  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

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
            <TabPanel activeTab={activeTab} onTabChange={setActiveTab}>
              {(tab): ReactNode => (
                <>
                  {/* TerminalTab is always mounted inside the content area to preserve
                      xterm state across tab switches. Hidden via CSS when not active. */}
                  <TerminalTab
                    key={activeSession.session_id}
                    sessionId={activeSession.session_id}
                    hidden={tab !== 'work'}
                    onRegister={(writeFn) => {
                      terminalWriters.current[activeSession.session_id] = writeFn
                    }}
                    onUnregister={() => {
                      delete terminalWriters.current[activeSession.session_id]
                    }}
                    onInput={(data) => wsRef.current?.send({
                      type: 'input',
                      session_id: activeSession.session_id,
                      data,
                    })}
                    onResize={(cols, rows) => wsRef.current?.send({
                      type: 'resize',
                      session_id: activeSession.session_id,
                      cols,
                      rows,
                    })}
                  />
                  {tab === 'tasks' && <TasksTab jobs={[]} />}
                  {tab === 'agent' && <AgentTab session={activeSession} role={activeRole} />}
                  {tab === 'tools' && <ToolsTab tools={activeSession.tools} />}
                </>
              )}
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
