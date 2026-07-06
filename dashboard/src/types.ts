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
