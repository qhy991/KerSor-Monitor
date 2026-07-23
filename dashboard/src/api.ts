import type {
  CreateTaskInput,
  Host,
  Project,
  Summary,
  TaskAction,
  TaskPoint,
  Task,
  TaskTelemetry,
  Template,
} from './types';

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export interface RequestOptions {
  signal?: AbortSignal;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function apiPath(path: string): string {
  return `${base}${path}`;
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return null;
  const messages = detail.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const record = item as { loc?: unknown; msg?: unknown };
    if (typeof record.msg !== 'string') return [];
    const location = Array.isArray(record.loc) ? record.loc.join('.') : '';
    return [location ? `${location}: ${record.msg}` : record.msg];
  });
  return messages.length > 0 ? messages.join('; ') : null;
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    if (!response.ok) return text;
    throw new ApiError(response.status, `Invalid JSON response from ${response.url || 'API'}`, text);
  }
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiPath(path), init);
  const body = await parseBody(response);
  if (!response.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? (body as { detail: unknown }).detail
        : body;
    const message =
      detailMessage(detail) ||
      (typeof detail === 'string' && detail) ||
      `${response.status} ${response.statusText || 'Request failed'}`;
    throw new ApiError(response.status, message, detail);
  }
  return body as T;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export async function getTaskHistory(
  tid: string,
  options: RequestOptions = {},
): Promise<{ task_id: string; points: TaskPoint[] }> {
  return requestJson(`/tasks/${encodeURIComponent(tid)}/history`, { signal: options.signal });
}

export async function listTasks(pid: string, options: RequestOptions = {}): Promise<Task[]> {
  return requestJson(`/projects/${encodeURIComponent(pid)}/tasks`, { signal: options.signal });
}

export async function getProjects(options: RequestOptions = {}): Promise<Project[]> {
  return requestJson('/projects', { signal: options.signal });
}

export async function createProject(
  pid: string,
  name: string = pid,
  opts: { feishu_base?: string | null; feishu_table?: string | null } = {},
  options: RequestOptions = {},
): Promise<{ id: string }> {
  return requestJson('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: pid, name, ...opts }),
    signal: options.signal,
  });
}

export async function deleteTask(
  tid: string,
  options: RequestOptions = {},
): Promise<{ deleted: string }> {
  return requestJson(`/tasks/${encodeURIComponent(tid)}`, {
    method: 'DELETE',
    signal: options.signal,
  });
}

export async function createTasks(
  pid: string,
  tasks: CreateTaskInput[],
  options: RequestOptions = {},
): Promise<{ created: number }> {
  return requestJson(`/projects/${encodeURIComponent(pid)}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tasks),
    signal: options.signal,
  });
}

export async function ensureProjectAndCreateTasks(
  pid: string,
  tasks: CreateTaskInput[],
  options: RequestOptions = {},
): Promise<{ created: number }> {
  try {
    return await createTasks(pid, tasks, options);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      await createProject(pid, pid, {}, options);
      return createTasks(pid, tasks, options);
    }
    throw e;
  }
}

export async function actuate(
  tid: string,
  action: TaskAction,
  payload: Record<string, unknown>,
  options: RequestOptions = {},
): Promise<{ ok: boolean; action: string }> {
  return requestJson(`/tasks/${encodeURIComponent(tid)}/actuate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, payload }),
    signal: options.signal,
  });
}

export interface ProjectSubscriptionHandlers {
  onEvent: (task: TaskTelemetry) => void;
  onOpen?: () => void;
  onError?: (error: Error) => void;
}

export function subscribeProject(
  pid: string,
  handlers: ProjectSubscriptionHandlers,
): EventSource {
  // One SSE stream per project — each message is a single task's latest snapshot.
  const es = new EventSource(apiPath(`/projects/${encodeURIComponent(pid)}/events`));
  es.onopen = () => handlers.onOpen?.();
  es.onerror = () => handlers.onError?.(new Error('Live updates disconnected; reconnecting…'));
  es.onmessage = (event: MessageEvent<string>) => {
    try {
      const task = JSON.parse(event.data) as unknown;
      if (!task || typeof task !== 'object' || typeof (task as { id?: unknown }).id !== 'string') {
        throw new Error('Live update did not contain a task id');
      }
      handlers.onEvent(task as TaskTelemetry);
    } catch (error) {
      handlers.onError?.(error instanceof Error ? error : new Error('Invalid live update'));
    }
  };
  return es;
}

// --- templates ---
export async function getTemplates(options: RequestOptions = {}): Promise<Template[]> {
  return requestJson('/templates', { signal: options.signal });
}
export async function createTemplate(
  t: { id: string; name: string; spec: string; runtime?: string; effort?: string; evaluator?: string | null },
  options: RequestOptions = {},
): Promise<{ id: string }> {
  return requestJson('/templates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(t),
    signal: options.signal,
  });
}
export async function deleteTemplate(
  id: string,
  options: RequestOptions = {},
): Promise<{ deleted: string }> {
  return requestJson(`/templates/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal: options.signal,
  });
}

// --- hosts (accessible hardware) ---
export async function getSummary(
  project?: string,
  options: RequestOptions = {},
): Promise<Summary> {
  const q = project ? `?project=${encodeURIComponent(project)}` : '';
  return requestJson(`/summary${q}`, { signal: options.signal });
}
export async function getHosts(options: RequestOptions = {}): Promise<Host[]> {
  return requestJson('/hosts', { signal: options.signal });
}
export async function createHost(h: {
  id: string;
  ssh_alias: string;
  remote_root: string;
  gpu?: string | null;
  notes?: string;
}, options: RequestOptions = {}): Promise<{ id: string }> {
  return requestJson('/hosts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(h),
    signal: options.signal,
  });
}
export async function deleteHost(
  id: string,
  options: RequestOptions = {},
): Promise<{ deleted: string }> {
  return requestJson(`/hosts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal: options.signal,
  });
}
