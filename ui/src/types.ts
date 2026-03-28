export interface AgentConfig {
  id: string
  name: string
  provider: string
  model: string
  system_prompt: string
  max_tokens: number
  orchestrates: string[]
}

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface Job {
  job_id: string
  agent_id: string
  title: string
  status: JobStatus
  progress: number | null
}

export interface ToolCallEvent {
  tool_id: string
  name: string
  input: Record<string, unknown>
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  tool_calls?: ToolCallEvent[]
}

export interface SessionState {
  messages: Message[]
  streamingMessages: Record<string, string>  // job_id → accumulated text
  jobs: Job[]
  input_tokens: number
  output_tokens: number
  cost_usd: number
  model: string
}

export type WSIncoming =
  | { type: 'token'; job_id: string; delta: string }
  | { type: 'job_update'; job_id: string; agent_id: string; title?: string; status: JobStatus; progress: number | null }
  | { type: 'tool_call'; job_id: string; tool_id: string; name: string; input: Record<string, unknown> }
  | { type: 'usage'; job_id: string; input_tokens: number; output_tokens: number; cost_usd: number }
  | { type: 'error'; job_id: string; message: string }
