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
  input_tokens: number
  output_tokens: number
}

// UI-side session state
export interface SessionState {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  input_tokens: number
  output_tokens: number
  cost_usd: number
  tools: ToolInfo[]
}

export interface ToolInfo {
  name: string
  description: string
}

// Task record (from WS task events)
export interface TaskRecord {
  task_id: string
  delegated_by: string
  delegated_to: string
  instructions: string
  caps_requested: string[]
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress_pct: number
  progress_msg: string
  result: string | Record<string, unknown> | null
  context: Record<string, unknown> | null
  callback: boolean
  created_at: string
  updated_at: string
}

// WebSocket: server → client
export type WSIncoming =
  | { type: 'output'; session_id: string; data: string }
  | { type: 'cost_update'; session_id: string; cost_usd: number; input_tokens: number; output_tokens: number }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }
  | { type: 'task.created'; task: TaskRecord }
  | { type: 'task.updated'; task: TaskRecord }
  | { type: 'task.completed'; task: TaskRecord }
  | { type: 'task.failed'; task: TaskRecord }

// WebSocket: client → server
export type WSSend =
  | { type: 'input'; session_id: string; data: string }
  | { type: 'resize'; session_id: string; cols: number; rows: number }
  | { type: 'cancel'; session_id: string }
