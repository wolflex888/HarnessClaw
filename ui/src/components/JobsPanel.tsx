import type { Job, JobStatus } from '../types'

interface Props {
  jobs: Job[]
}

const STATUS_STYLES: Record<JobStatus, string> = {
  queued:    'text-gray-400',
  running:   'text-blue-400',
  completed: 'text-green-400',
  failed:    'text-red-400',
}

const STATUS_LABELS: Record<JobStatus, string> = {
  queued:    '◌ Queued',
  running:   '● Running',
  completed: '✓ Done',
  failed:    '✗ Failed',
}

export function JobsPanel({ jobs }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="w-36 shrink-0 border-l border-gray-800 p-3">
        <div className="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2">
          Jobs
        </div>
        <div className="text-xs text-gray-600">No jobs yet</div>
      </div>
    )
  }

  return (
    <div className="w-36 shrink-0 border-l border-gray-800 flex flex-col h-full">
      <div className="px-3 py-3 border-b border-gray-800">
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
          Jobs
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
        {[...jobs].reverse().map((job) => (
          <div
            key={job.job_id}
            className="bg-gray-900 border border-gray-800 rounded-md p-2"
            style={{ borderLeftColor: job.status === 'running' ? '#3b82f6' : undefined, borderLeftWidth: job.status === 'running' ? '2px' : undefined }}
          >
            <div className="text-xs text-gray-300 truncate mb-1">{job.title || job.agent_id}</div>
            <div className={`text-xs ${STATUS_STYLES[job.status]}`}>
              {STATUS_LABELS[job.status]}
            </div>
            {job.status === 'running' && (
              <div className="mt-1.5 h-1 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 rounded-full transition-all duration-300"
                  style={{ width: job.progress != null ? `${job.progress}%` : '40%', animation: job.progress == null ? 'pulse 1.5s ease-in-out infinite' : undefined }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
