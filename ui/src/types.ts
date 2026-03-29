// Role template (from /api/roles)
export interface RoleConfig {
  id: string
  name: string
  provider: string
  model: string
  system_prompt: string
  max_tokens: number
}

// Session (from /api/sessions)
export interface SessionData {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  claude_session_id: string | null
  messages: Array<{ role: string; content: string }>
  input_tokens: number
  output_tokens: number
}

// Job/Task tracked in UI
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface Job {
  job_id: string
  session_id: string
  title: string
  status: JobStatus
  progress: number | null
}

// Pending permission request
export interface PendingPermission {
  request_id: string
  tool_name: string
  input: Record<string, unknown>
}

// UI-side session state
export interface SessionState {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  messages: Message[]
  streamingMessages: Record<string, string>
  jobs: Job[]
  input_tokens: number
  output_tokens: number
  cost_usd: number
  tools: ToolInfo[]
  pendingPermissions: PendingPermission[]
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCallEvent[]
}

export interface ToolCallEvent {
  tool_id: string
  name: string
  input: Record<string, unknown>
}

export interface ToolInfo {
  name: string
  description: string
}

// WebSocket: server → client
export type WSIncoming =
  | { type: 'token'; job_id: string; delta: string }
  | { type: 'job_update'; job_id: string; session_id: string; status: JobStatus; progress: number | null; title: string }
  | { type: 'tool_call'; job_id: string; tool_name: string; input: Record<string, unknown> }
  | { type: 'usage'; job_id: string; input_tokens: number; output_tokens: number; cost_usd: number }
  | { type: 'error'; job_id: string; message: string }
  | { type: 'permission_request'; session_id: string; request_id: string; tool_name: string; input: Record<string, unknown> }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }

// WebSocket: client → server
export type WSSend =
  | { type: 'chat'; session_id: string; text: string }
  | { type: 'cancel'; session_id: string }
  | { type: 'resume'; session_id: string }
  | { type: 'permission_response'; request_id: string; approved: boolean }
