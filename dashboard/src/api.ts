import type { Task } from './types';
const base = '';
export async function listTasks(pid: string): Promise<Task[]> {
  const r = await fetch(`${base}/projects/${pid}/tasks`); return r.json();
}
export async function actuate(tid: string, action: string, payload: object) {
  await fetch(`${base}/tasks/${tid}/actuate`, { method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action, payload }) });
}
export function subscribe(tid: string, onEvt: (t: Task) => void) {
  const es = new EventSource(`${base}/tasks/${tid}/events`);
  es.onmessage = (e) => onEvt(JSON.parse(e.data));
  return es;
}
