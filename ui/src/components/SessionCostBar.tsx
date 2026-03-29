interface Props {
  model: string
  inputTokens: number
  outputTokens: number
  costUsd: number
  sessionName: string
  status: 'idle' | 'running' | 'killed'
}

export function SessionCostBar({ model, inputTokens, outputTokens, costUsd, sessionName, status }: Props) {
  const totalTokens = inputTokens + outputTokens

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 bg-gray-900 text-xs text-gray-400">
      <div className="flex items-center gap-2">
        <span className={status === 'running' ? 'text-blue-400' : status === 'killed' ? 'text-red-400' : 'text-gray-500'}>
          {status === 'running' ? '●' : status === 'killed' ? '✕' : '○'}
        </span>
        <span className="text-gray-300 font-medium truncate max-w-xs">{sessionName || 'New session'}</span>
        <span className="text-gray-600">·</span>
        <span>{model}</span>
      </div>
      <div className="flex items-center gap-3">
        <span>{totalTokens.toLocaleString()} tokens</span>
        <span className="text-green-400">${costUsd.toFixed(4)}</span>
      </div>
    </div>
  )
}
