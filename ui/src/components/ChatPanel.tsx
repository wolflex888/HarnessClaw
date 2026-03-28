import { useEffect, useRef } from 'react'
import type { Message, ToolCallEvent } from '../types'

interface Props {
  messages: Message[]
  streamingMessages: Record<string, string>
  onSend: (text: string) => void
  disabled?: boolean
}

function ToolCallCard({ toolCall }: { toolCall: ToolCallEvent }) {
  return (
    <div className="border border-gray-700 rounded-md p-3 bg-gray-900 text-xs my-1">
      <div className="text-yellow-400 font-medium mb-1">→ Calling: {toolCall.name}</div>
      <div className="text-gray-500 font-mono truncate">
        {JSON.stringify(toolCall.input).slice(0, 120)}
      </div>
    </div>
  )
}

export function ChatPanel({ messages, streamingMessages, onSend, disabled }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessages])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const text = inputRef.current?.value.trim()
      if (text) {
        onSend(text)
        inputRef.current!.value = ''
      }
    }
  }

  const streamingEntries = Object.entries(streamingMessages)

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col gap-1 max-w-[80%] ${
              msg.role === 'user' ? 'self-end items-end' : 'self-start items-start'
            }`}
          >
            {msg.tool_calls?.map((tc) => <ToolCallCard key={tc.tool_id} toolCall={tc} />)}
            <div
              className={`rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-900/30 text-gray-200'
                  : 'bg-gray-800 text-gray-200'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {/* Active streaming messages */}
        {streamingEntries.map(([jobId, text]) => (
          <div key={jobId} className="self-start max-w-[80%]">
            <div className="rounded-lg px-3 py-2 text-sm bg-gray-800 text-gray-200 whitespace-pre-wrap">
              {text}
              <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-blue-400 animate-pulse align-middle" />
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-800 p-3 flex gap-2">
        <textarea
          ref={inputRef}
          rows={1}
          disabled={disabled}
          onKeyDown={handleKeyDown}
          placeholder="Message… (Enter to send, Shift+Enter for newline)"
          className="flex-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-none focus:outline-none focus:border-blue-600"
        />
        <button
          onClick={() => {
            const text = inputRef.current?.value.trim()
            if (text) {
              onSend(text)
              inputRef.current!.value = ''
            }
          }}
          disabled={disabled}
          className="px-4 py-2 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded-md transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  )
}
