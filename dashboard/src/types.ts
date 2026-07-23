export interface TaskMetadata {
  effort?: string;
  command?: string;
  [key: string]: unknown;
}

export type TaskState =
  | 'PLANNED'
  | 'QUEUED'
  | 'DISPATCHING'
  | 'RUNNING'
  | 'PAUSED'
  | 'DONE'
  | 'FAILED'
  | 'STUCK'
  | 'CANCELLED'
  | 'LOST';

// Stable persisted fields present in the canonical REST task view.
export interface TaskRecord {
  id: string;
  name: string;
  state: TaskState;
  runtime: string;
  project_id?: string;
  spec?: string;
  owner?: string | null;
  target_host?: string | null;
  workspace_path?: string | null;
  evaluator?: string | null;
  resource_req?: Record<string, unknown>;
  metadata?: TaskMetadata;
  created_at?: string;
  updated_at?: string;
}

// SSE messages are deltas and are intentionally partial. Keeping this separate
// from TaskRecord prevents a partial live event being mistaken for a complete
// task returned by REST.
export interface TaskTelemetry {
  id: string;
  deleted?: boolean;
  name?: string;
  state?: TaskState;
  runtime?: string;
  owner?: string | null;
  target_host?: string | null;
  workspace_path?: string | null;
  metadata?: TaskMetadata;
  speedup?: number | null;
  rounds?: number | null;
  candidates?: number | null;
  session_uuid?: string | null;
  last_activity?: string | null;
  last_tool?: string | null;
  tokens?: number | null;
  pane_tail?: string | null;
  timestamp?: string | null;
  status_state?: string | null;
  best_candidate?: string | null;
  exited?: boolean;
  source?: string | null;
}

// The dashboard view is a persisted record enriched with any telemetry received
// for that known task.
export interface Task extends TaskRecord {
  speedup?: number | null;
  rounds?: number | null;
  candidates?: number | null;
  session_uuid?: string | null;
  last_activity?: string | null;
  last_tool?: string | null;
  tokens?: number | null;
  pane_tail?: string | null;
  timestamp?: string | null;
  status_state?: string | null;
  best_candidate?: string | null;
  exited?: boolean;
  source?: string | null;
}

export interface Project {
  id: string;
  name: string;
  feishu_configured?: boolean;
  created_at?: string;
}

export interface TaskPoint {
  ts: string;
  state: string | null;
  speedup: number | null;
  rounds: number | null;
  candidates: number | null;
  last_tool?: string | null;
  last_activity?: string | null;
  tokens?: number | null;
}

export interface Host {
  id: string;
  ssh_alias: string;
  remote_root: string;
  gpu?: string | null;
  notes?: string;
}

export interface Summary {
  total: number;
  running: number;
  dispatching?: number;
  done: number;
  stuck: number;
  queued: number;
  failed: number;
  paused: number;
  cancelled?: number;
  lost?: number;
}

export interface Template {
  id: string;
  name: string;
  spec: string;
  runtime: string;
  effort: string;
  evaluator: string | null;
  builtin: boolean;
  created_at?: string;
}

export interface CreateTaskInput {
  id: string;
  name: string;
  spec: string;
  runtime: string;
  evaluator?: string | null;
  owner?: string | null;
  target_host?: string | null;
  metadata?: TaskMetadata;
}

export type TaskAction = 'nudge' | 'pause' | 'resume' | 'stop';
