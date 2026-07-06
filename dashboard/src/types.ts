export interface Task {
  id: string;
  name: string;
  state: string;
  speedup: number | null;
  rounds: number;
  candidates: number;
  runtime: string;
  target_host?: string | null;
  session_uuid?: string | null;
}

export interface Host {
  id: string;
  ssh_alias: string;
  remote_root: string;
  gpu?: string | null;
  notes?: string;
}
