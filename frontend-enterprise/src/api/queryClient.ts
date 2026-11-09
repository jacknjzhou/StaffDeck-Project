import { QueryClient } from '@tanstack/react-query';

/**
 * 全局查询默认策略：30 秒内视为新鲜、失败不重试、窗口聚焦不自动刷新。
 *
 * 单独导出以便测试构造行为一致的 QueryClient，避免默认值出现第二份拷贝。
 */
export const queryClientDefaults = {
  staleTime: 30_000,
  retry: false,
  refetchOnWindowFocus: false,
} as const;

/** 全局查询缓存实例，默认策略见 {@link queryClientDefaults}。 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { ...queryClientDefaults },
  },
});
