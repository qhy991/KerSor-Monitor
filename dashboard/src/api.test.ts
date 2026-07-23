import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, requestJson } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('requestJson', () => {
  it('throws ApiError with a FastAPI string detail on non-2xx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'project not found' }), {
          status: 404,
          statusText: 'Not Found',
        }),
      ),
    );

    await expect(requestJson('/projects/missing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'project not found',
    });
  });

  it('formats FastAPI validation details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ loc: ['body', 0, 'id'], msg: 'Field required', type: 'missing' }],
          }),
          { status: 422, statusText: 'Unprocessable Entity' },
        ),
      ),
    );

    const request = requestJson('/projects/p/tasks');
    await expect(request).rejects.toBeInstanceOf(ApiError);
    await expect(request).rejects.toMatchObject({
      status: 422,
      message: 'body.0.id: Field required',
    });
  });

  it('forwards AbortSignal to fetch', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: 'p1', name: 'Project' }]), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await requestJson('/projects', { signal: controller.signal });

    expect(fetchMock).toHaveBeenCalledWith('/projects', { signal: controller.signal });
  });
});
