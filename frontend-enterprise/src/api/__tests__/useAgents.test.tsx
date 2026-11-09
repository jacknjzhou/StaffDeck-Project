// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useAgents } from '../useAgents';

/** 构造仅实现 text/json 的伪 Response，供 api client 解析。 */
function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

/** 创建关闭重试的测试用 QueryClient。 */
function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

/** 生成注入指定 QueryClient 的 wrapper。 */
function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useAgents', () => {
  /** 同一 QueryClient 下的两个消费者共享同一次请求。 */
  it('dedupes requests for the same tenant', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ id: 'a1' }]));
    vi.stubGlobal('fetch', fetchMock);
    const client = createClient();
    const { result } = renderHook(
      () => [useAgents('t1'), useAgents('t1')],
      { wrapper: wrapperFor(client) },
    );

    await waitFor(() => expect(result.current[0].data).toHaveLength(1));

    expect(result.current[1].data).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/enterprise/agents?tenant_id=t1',
    );
  });

  /** 新挂载的消费者在 staleTime 内直接复用缓存，不再请求网络。 */
  it('serves a later consumer from the same cache without another request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ id: 'a1' }]));
    vi.stubGlobal('fetch', fetchMock);
    const client = createClient();

    const first = renderHook(() => useAgents('t1'), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(first.result.current.data).toHaveLength(1));

    const second = renderHook(() => useAgents('t1'), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(second.result.current.data).toHaveLength(1));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
