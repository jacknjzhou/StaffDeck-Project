import { QueryClient } from '@tanstack/react-query';

/**
 * 全局查询缓存实例。
 *
 * 默认 30 秒内视为新鲜、失败不重试、窗口聚焦不自动刷新，
 * 避免多页面重复拉取同一份服务端数据。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});
