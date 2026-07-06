import type { Task } from './types';
const base = '';

export async function listTasks(pid: string): Promise<Task[]> {
  const r = await fetch(`${base}/projects/${pid}/tasks`);
  return r.json();
}

export async function createProject(pid: string, name: string = pid): Promise<void> {
  await fetch(`${base}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: pid, name }),
  });
}

export async function createTasks(
  pid: string,
  tasks: {
    id: string;
    name: string;
    spec: string;
    runtime: string;
    evaluator?: string | null;
    target_host?: string | null;
  }[],
): Promise<{ created: number }> {
  const r = await fetch(`${base}/projects/${pid}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tasks),
  });
  if (!r.ok) {
    const e = new Error(`createTasks ${r.status}`);
    (e as Error & { status: number }).status = r.status;
    throw e;
  }
  return r.json();
}

/** Create the project if it doesn't exist, then post the task(s). */
export async function ensureProjectAndCreateTasks(
  pid: string,
  tasks: {
    id: string;
    name: string;
    spec: string;
    runtime: string;
    evaluator?: string | null;
    target_host?: string | null;
  }[],
): Promise<{ created: number }> {
  try {
    return await createTasks(pid, tasks);
  } catch (e) {
    const status = (e as Error & { status?: number }).status;
    if (status === 404) {
      await createProject(pid);
      return await createTasks(pid, tasks);
    }
    throw e;
  }
}

export async function actuate(tid: string, action: string, payload: object): Promise<void> {
  await fetch(`${base}/tasks/${tid}/actuate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, payload }),
  });
}

export function subscribe(tid: string, onEvt: (t: Task) => void): EventSource {
  const es = new EventSource(`${base}/tasks/${tid}/events`);
  es.onmessage = (e) => onEvt(JSON.parse(e.data));
  return es;
}
