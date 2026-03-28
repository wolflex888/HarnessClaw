interface Props {
  model: string
  inputTokens: number
  outputTokens: number
  costUsd: number
}

export function SessionCostBar({ model, inputTokens, outputTokens, costUsd }: Props) {
  const totalTokens = inputTokens + outputTokens

  return (
    <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between text-xs text-gray-500">
      <span className="font-mono">{model || '—'}</span>
      <div className="flex items-center gap-4">
        <span>{totalTokens.toLocaleString()} tokens</span>
        <span className="text-green-500 font-medium">
          ${costUsd.toFixed(4)}
        </span>
      </div>
    </div>
  )
}
