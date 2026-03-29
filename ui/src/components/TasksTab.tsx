import type { Job } from '../types'

interface Props {
  jobs: Job[]
}

function statusBadge(status: string): string {
  if (status === 'running') return '● Running'
  if (status === 'completed') return '✓ Done'
  if (status === 'failed') return '✕ Failed'
  return '◌ Queued'
}

function statusColor(status: string): string {
  if (status === 'running') return 'text-blue-400'
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-gray-500'
}

export function TasksTab({ jobs }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        No tasks yet
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2">
      {[...jobs].reverse().map((job) => (
        <div key={job.job_id} className="bg-gray-800 rounded-lg p-3 flex flex-col gap-1.5 border border-gray-700">
          <div className="text-sm text-gray-200 truncate">{job.title || job.job_id}</div>
          <div className={`text-xs ${statusColor(job.status)}`}>{statusBadge(job.status)}</div>
          {job.status === 'running' && (
            <div className="h-1 bg-gray-700 rounded-full overflow-hidden">
              {job.progress !== null ? (
                <div
                  className="h-full bg-blue-500 rounded-full transition-all"
                  style={{ width: `${job.progress}%` }}
                />
              ) : (
                <div className="h-full bg-blue-500 rounded-full animate-pulse w-1/2" />
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
