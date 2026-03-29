import { useEffect, useRef, useCallback } from 'react'
import type { Message, PendingPermission } from '../types'
import { PermissionDialog } from './PermissionDialog'

interface Props {
  messages: Message[]
  streamingMessages: Record<string, string>
  pendingPermissions: PendingPermission[]
  onSend: (text: string) => void
  onAllow: (requestId: string) => void
  onDeny: (requestId: string) => void
}

export function WorkTab({ messages, streamingMessages, pendingPermissions, onSend, onAllow, onDeny }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessages, pendingPermissions])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const text = e.currentTarget.value.trim()
      if (text) {
        onSend(text)
        e.currentTarget.value = ''
      }
    }
  }, [onSend])

  const streamingEntries = Object.entries(streamingMessages)

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`max-w-3xl ${msg.role === 'user' ? 'self-end' : 'self-start'}`}
          >
            {msg.tool_calls?.length ? (
              <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm text-gray-300">
                {msg.tool_calls.map((tc) => (
                  <div key={tc.tool_id} className="text-yellow-400">→ {tc.name}</div>
                ))}
              </div>
            ) : (
              <div
                className={`rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-blue-700 text-white'
                    : 'bg-gray-800 text-gray-100'
                }`}
              >
                {msg.content}
              </div>
            )}
          </div>
        ))}

        {pendingPermissions.map((p) => (
          <PermissionDialog key={p.request_id} permission={p} onAllow={onAllow} onDeny={onDeny} />
        ))}

        {streamingEntries.map(([jobId, text]) => (
          <div key={jobId} className="self-start max-w-3xl bg-gray-800 text-gray-100 rounded-lg px-4 py-2 text-sm whitespace-pre-wrap">
            {text}
            <span className="inline-block w-1.5 h-4 bg-gray-400 ml-0.5 animate-pulse align-middle" />
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      <div className="p-3 border-t border-gray-800 flex gap-2">
        <textarea
          rows={1}
          placeholder="Message..."
          className="flex-1 bg-gray-800 text-white text-sm rounded px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
          onKeyDown={handleKeyDown}
        />
      </div>
    </div>
  )
}
