import { useState, useEffect, useRef, useCallback, type MutableRefObject } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import type { TaskRecord, SessionState } from '../types'

interface Props {
  tasks: TaskRecord[]
  sessions: Record<string, SessionState>
  terminalWriters: MutableRefObject<Record<string, (data: Uint8Array) => void>>
  onInput: (sessionId: string, data: string) => void
  onResize: (sessionId: string, cols: number, rows: number) => void
  onRetry: (taskId: string) => void
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
      <div
        className="h-full bg-blue-500 rounded-full transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function statusBadge(status: TaskRecord['status']) {
  if (status === 'running') return '● Running'
  if (status === 'completed') return '✓ Done'
  if (status === 'failed') return '✕ Failed'
  return '◌ Queued'
}

function statusColor(status: TaskRecord['status']) {
  if (status === 'running') return 'text-blue-400'
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-gray-500'
}

function priorityLabel(priority: number): string {
  if (priority === 1) return '↑ High'
  if (priority === 3) return '↓ Low'
  return '→ Normal'
}

function priorityColor(priority: number): string {
  if (priority === 1) return 'text-red-400'
  if (priority === 3) return 'text-gray-500'
  return 'text-gray-400'
}

function TaskTerminalPanel({ sessionId, terminalWriters }: {
  sessionId: string
  terminalWriters: MutableRefObject<Record<string, (data: Uint8Array) => void>>
}) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const term = new Terminal({
      theme: { background: '#111827' },
      fontSize: 12,
      convertEol: true,
      disableStdin: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current)
    fit.fit()

    // Register a secondary writer for this session so task panel also receives output
    const key = `task:${sessionId}`
    const existingWriter = terminalWriters.current[key]
    terminalWriters.current[key] = (data: Uint8Array) => {
      term.write(data)
      existingWriter?.(data)
    }

    return () => {
      delete terminalWriters.current[key]
      term.dispose()
    }
  }, [sessionId])

  return (
    <div
      ref={containerRef}
      className="h-48 rounded bg-gray-900 overflow-hidden"
    />
  )
}

function TaskRow({ task, sessions, terminalWriters, expanded, onToggle, onRetry }: {
  task: TaskRecord
  sessions: Record<string, SessionState>
  terminalWriters: MutableRefObject<Record<string, (data: Uint8Array) => void>>
  expanded: boolean
  onToggle: () => void
  onRetry: (taskId: string) => void
}) {
  const agentSession = sessions[task.delegated_to]
  const agentName = agentSession?.name || task.delegated_to.slice(0, 8)

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-3 bg-gray-800 hover:bg-gray-750 text-left"
      >
        <span className="text-gray-500 text-xs w-4">{expanded ? '▼' : '▶'}</span>
        <span className="text-xs text-gray-400 font-mono w-20 truncate">{task.task_id.slice(0, 8)}</span>
        <span className="text-sm text-gray-200 flex-1 truncate">{agentName}</span>
        <div className="w-24">
          {task.status === 'running' && <ProgressBar pct={task.progress_pct} />}
        </div>
        <span className={`text-xs w-20 text-right ${statusColor(task.status)}`}>
          {statusBadge(task.status)}
        </span>
        <span className={`text-xs w-16 ${priorityColor(task.priority ?? 2)}`}>
          {priorityLabel(task.priority ?? 2)}
        </span>
      </button>

      {expanded && (
        <div className="p-3 bg-gray-900 border-t border-gray-700 flex flex-col gap-2">
          <div className="flex gap-4 text-xs text-gray-500">
            <span>from: {task.delegated_by.slice(0, 8)}</span>
            <span>caps: {task.caps_requested.join(', ')}</span>
            {task.progress_msg && <span>{task.progress_msg}</span>}
          </div>
          <TaskTerminalPanel sessionId={task.delegated_to} terminalWriters={terminalWriters} />
          {task.result && (
            <div className={`text-xs bg-gray-800 rounded p-2 whitespace-pre-wrap ${task.status === 'failed' ? 'text-red-400' : 'text-green-400'}`}>
              {typeof task.result === 'string' ? task.result : JSON.stringify(task.result, null, 2)}
            </div>
          )}
          {task.status === 'failed' && (
            <button
              onClick={() => onRetry(task.task_id)}
              className="self-start text-xs text-yellow-400 hover:text-yellow-300 px-2 py-1 border border-yellow-800 rounded"
            >
              ↺ Retry
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function TasksTab({ tasks, sessions, terminalWriters, onInput: _onInput, onResize: _onResize, onRetry }: Props) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const toggle = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  if (tasks.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        No tasks yet
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
      {[...tasks].reverse().map(task => (
        <TaskRow
          key={task.task_id}
          task={task}
          sessions={sessions}
          terminalWriters={terminalWriters}
          expanded={expandedIds.has(task.task_id)}
          onToggle={() => toggle(task.task_id)}
          onRetry={onRetry}
        />
      ))}
    </div>
  )
}
