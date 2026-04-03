// ui/src/components/WorkflowsTab.tsx
import { useState, useEffect, useCallback } from 'react'
import type { WorkflowDefinition, WorkflowRun } from '../types'

interface Props {
  runs: WorkflowRun[]
  onRunWorkflow: (workflowId: string, input: string) => Promise<void>
}

function statusColor(status: WorkflowRun['status']): string {
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-yellow-400'
}

export function WorkflowsTab({ runs, onRunWorkflow }: Props) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([])
  const [selectedDef, setSelectedDef] = useState<WorkflowDefinition | null>(null)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const [showRunModal, setShowRunModal] = useState(false)
  const [runInput, setRunInput] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetch('/api/workflows')
      .then(r => r.json())
      .then(setDefinitions)
      .catch(console.error)
  }, [])

  const handleRunSubmit = useCallback(async () => {
    if (!selectedDef || !runInput.trim()) return
    setSubmitting(true)
    await onRunWorkflow(selectedDef.id, runInput.trim())
    setRunInput('')
    setShowRunModal(false)
    setSubmitting(false)
  }, [selectedDef, runInput, onRunWorkflow])

  const sortedRuns = [...runs].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left panel: workflow definitions */}
      <div className="w-64 border-r border-gray-800 flex flex-col overflow-y-auto">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide">Workflows</div>
        {definitions.map(def => (
          <div key={def.id}>
            <button
              onClick={() => setSelectedDef(selectedDef?.id === def.id ? null : def)}
              className={`w-full px-3 py-2 text-left border-b border-gray-800 hover:bg-gray-850 ${
                selectedDef?.id === def.id ? 'bg-gray-800' : ''
              }`}
            >
              <div className="text-sm text-gray-200">{def.name}</div>
              <div className="text-xs text-gray-500">{def.steps.length} steps</div>
            </button>
            {selectedDef?.id === def.id && (
              <div className="bg-gray-900 border-b border-gray-800 px-3 py-2">
                {def.steps.map((step, i) => (
                  <div key={step.id} className="text-xs text-gray-400 py-0.5 flex items-center gap-1">
                    <span className="text-gray-600">{i + 1}.</span>
                    <span className="font-mono text-gray-300">{step.id}</span>
                    <span className="text-gray-600">→</span>
                    <span>{step.caps.join(', ')}</span>
                  </div>
                ))}
                <button
                  onClick={() => setShowRunModal(true)}
                  className="mt-2 w-full text-xs bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 rounded"
                >
                  Run
                </button>
              </div>
            )}
          </div>
        ))}
        {definitions.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">No workflows defined</div>
        )}
      </div>

      {/* Right panel: workflow runs */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide border-b border-gray-800">
          Recent Runs
        </div>
        <div className="flex-1 overflow-y-auto">
          {sortedRuns.map(run => (
            <div key={run.run_id} className="border-b border-gray-800">
              <button
                onClick={() => setExpandedRun(expandedRun === run.run_id ? null : run.run_id)}
                className="w-full px-3 py-2 text-left hover:bg-gray-850 flex items-center justify-between"
              >
                <div>
                  <div className="text-sm text-gray-200">{run.workflow_id}</div>
                  <div className="text-xs text-gray-500">
                    by {run.initiated_by} · {new Date(run.created_at).toLocaleString()}
                  </div>
                </div>
                <span className={`text-xs font-medium ${statusColor(run.status)}`}>
                  {run.status}
                </span>
              </button>
              {expandedRun === run.run_id && (
                <div className="px-4 pb-3 bg-gray-900">
                  <div className="text-xs text-gray-500 mb-1">
                    Input: <span className="text-gray-300">{run.input || '—'}</span>
                  </div>
                  <div className="text-xs text-gray-500 mb-1">
                    Current step: <span className="font-mono text-gray-300">{run.current_step_id}</span>
                  </div>
                  {Object.entries(run.step_results).map(([stepId, result]) => (
                    <div key={stepId} className="mt-1">
                      <div className="text-xs text-gray-500 font-mono">{stepId}:</div>
                      <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono pl-2 max-h-24 overflow-y-auto">
                        {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
                      </pre>
                    </div>
                  ))}
                  {Object.keys(run.step_results).length === 0 && (
                    <div className="text-xs text-gray-600 mt-1">No steps completed yet</div>
                  )}
                </div>
              )}
            </div>
          ))}
          {sortedRuns.length === 0 && (
            <div className="px-3 py-4 text-xs text-gray-600">
              No runs yet. Select a workflow and click Run.
            </div>
          )}
        </div>
      </div>

      {/* Run modal */}
      {showRunModal && selectedDef && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 w-96">
            <div className="text-sm text-white mb-3">Run: {selectedDef.name}</div>
            <textarea
              className="w-full bg-gray-800 text-gray-200 text-sm p-2 rounded border border-gray-700 resize-none h-24 focus:outline-none focus:border-blue-500"
              placeholder="Describe what you want the workflow to do..."
              value={runInput}
              onChange={e => setRunInput(e.target.value)}
              autoFocus
            />
            <div className="flex gap-2 mt-3 justify-end">
              <button
                onClick={() => { setShowRunModal(false); setRunInput('') }}
                className="text-xs text-gray-400 hover:text-gray-200 px-3 py-1.5 border border-gray-700 rounded"
              >
                Cancel
              </button>
              <button
                onClick={handleRunSubmit}
                disabled={!runInput.trim() || submitting}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white px-3 py-1.5 rounded"
              >
                {submitting ? 'Starting...' : 'Start'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
