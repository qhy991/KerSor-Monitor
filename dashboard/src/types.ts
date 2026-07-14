export interface Task {
  id: string;
  name: string;
  state: string;
  speedup: number | null;
  rounds: number;
  candidates: number;
  runtime: string;
  owner?: string | null;
  target_host?: string | null;
  session_uuid?: string | null;
  last_activity?: string;
  last_tool?: string | null;
  tokens?: number;
  pane_tail?: string;
  metadata?: { effort?: string; [key: string]: unknown };
}

export interface Project {
  id: string;
  name: string;
  feishu_base?: string | null;
  feishu_table?: string | null;
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
  done: number;
  stuck: number;
  queued: number;
  failed: number;
  paused: number;
}

export interface Template {
  id: string;
  name: string;
  spec: string;
  runtime: string;
  effort: string;
  evaluator: string | null;
  builtin: boolean;
}
