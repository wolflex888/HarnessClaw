import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react'
import { WsClient } from './ws'
import type { RoleConfig, SessionState, WSIncoming, TaskRecord, ToolInfo, WorkflowRun } from './types'
import { SessionSidebar } from './components/SessionSidebar'
import { SessionCreatePanel } from './components/SessionCreatePanel'
import { SessionCostBar } from './components/SessionCostBar'
import { TabPanel, type TabId } from './components/TabPanel'
import { TerminalTab } from './components/TerminalTab'
import { TasksTab } from './components/TasksTab'
import { AgentTab } from './components/AgentTab'
import { ToolsTab } from './components/ToolsTab'
import { MemoryTab } from './components/MemoryTab'
import { AuditTab } from './components/AuditTab'
import { WorkflowsTab } from './components/WorkflowsTab'

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
  const [tasks, setTasks] = useState<Record<string, TaskRecord>>({})
  const [mcpTools, setMcpTools] = useState<ToolInfo[]>([])
  const [workflowRuns, setWorkflowRuns] = useState<Record<string, WorkflowRun>>({})
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [activeTab, setActiveTab] = useState<TabId>('work')
  const wsRef = useRef<WsClient | null>(null)
  // Maps session_id → the xterm write function for that terminal
  const terminalWriters = useRef<Record<string, (data: Uint8Array) => void>>({})

  // Load roles + sessions from REST
  useEffect(() => {
    fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
    fetch('/api/mcp/tools').then(r => r.json()).then(setMcpTools).catch(console.error)
    fetch('/api/tasks')
      .then(r => r.json())
      .then((taskList: TaskRecord[]) => {
        const taskMap: Record<string, TaskRecord> = {}
        for (const t of taskList) taskMap[t.task_id] = t
        setTasks(taskMap)
      })
      .catch(console.error)
    fetch('/api/workflows/runs')
      .then(r => r.json())
      .then((runList: WorkflowRun[]) => {
        const runMap: Record<string, WorkflowRun> = {}
        for (const r of runList) runMap[r.run_id] = r
        setWorkflowRuns(runMap)
      })
      .catch(console.error)
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
      const binary = atob(msg.data)
      const bytes = Uint8Array.from(binary, c => c.charCodeAt(0))
      const writeFn = terminalWriters.current[msg.session_id]
      if (writeFn) writeFn(bytes)
      const taskWriteFn = terminalWriters.current[`task:${msg.session_id}`]
      if (taskWriteFn) taskWriteFn(bytes)
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
    } else if (
      msg.type === 'task.created' ||
      msg.type === 'task.updated' ||
      msg.type === 'task.completed' ||
      msg.type === 'task.failed'
    ) {
      setTasks(prev => ({ ...prev, [msg.task.task_id]: msg.task }))
    } else if (msg.type === 'workflow.started') {
      setWorkflowRuns(prev => ({
        ...prev,
        [msg.run_id]: {
          ...(prev[msg.run_id] ?? {}),
          run_id: msg.run_id,
          workflow_id: msg.workflow_id,
          status: 'running' as const,
          current_step_id: msg.step_id,
          step_results: {},
          input: '',
          initiated_by: '',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      }))
    } else if (msg.type === 'workflow.step') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return {
          ...prev,
          [msg.run_id]: {
            ...existing,
            step_results: { ...existing.step_results, [msg.step_id]: msg.result },
          },
        }
      })
    } else if (msg.type === 'workflow.completed') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return { ...prev, [msg.run_id]: { ...existing, status: 'completed' } }
      })
    } else if (msg.type === 'workflow.failed') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return { ...prev, [msg.run_id]: { ...existing, status: 'failed' } }
      })
    }
  }, [])

  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  const handleRunWorkflow = useCallback(async (workflowId: string, input: string) => {
    const res = await fetch(`/api/workflows/${workflowId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, initiated_by: 'dashboard' }),
    })
    if (!res.ok) console.error('workflow run failed', res.status, await res.text())
  }, [])

  const handleRetry = useCallback(async (taskId: string) => {
    const res = await fetch(`/api/tasks/${taskId}/retry`, { method: 'POST' })
    if (!res.ok) console.error('retry failed', res.status, await res.text())
    // new task arrives via WS task.created — no additional state update needed
  }, [])

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
                  {/* One TerminalTab per session, all always mounted to preserve xterm
                      history across session switches. Hidden via CSS when not active. */}
                  {Object.values(sessions).map(session => (
                    <TerminalTab
                      key={session.session_id}
                      sessionId={session.session_id}
                      hidden={tab !== 'work' || session.session_id !== activeSessionId}
                      onRegister={(writeFn) => {
                        terminalWriters.current[session.session_id] = writeFn
                      }}
                      onUnregister={() => {
                        delete terminalWriters.current[session.session_id]
                      }}
                      onInput={(data) => wsRef.current?.send({
                        type: 'input',
                        session_id: session.session_id,
                        data,
                      })}
                      onResize={(cols, rows) => wsRef.current?.send({
                        type: 'resize',
                        session_id: session.session_id,
                        cols,
                        rows,
                      })}
                    />
                  ))}
                  {tab === 'tasks' && (
                    <TasksTab
                      tasks={Object.values(tasks)}
                      sessions={sessions}
                      terminalWriters={terminalWriters}
                      onInput={(sessionId, data) => wsRef.current?.send({ type: 'input', session_id: sessionId, data })}
                      onResize={(sessionId, cols, rows) => wsRef.current?.send({ type: 'resize', session_id: sessionId, cols, rows })}
                      onRetry={handleRetry}
                    />
                  )}
                  {tab === 'agent' && <AgentTab session={activeSession} role={activeRole} />}
                  {tab === 'tools' && <ToolsTab tools={mcpTools} />}
                  {tab === 'memory' && <MemoryTab />}
                  {tab === 'audit' && <AuditTab />}
                  {tab === 'workflows' && (
                    <WorkflowsTab
                      runs={Object.values(workflowRuns)}
                      onRunWorkflow={handleRunWorkflow}
                    />
                  )}
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
