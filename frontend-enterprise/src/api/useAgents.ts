import { useQuery } from '@tanstack/react-query';

import type { AgentProfileRead } from '../types';
import { api } from './client';

/**
 * 读取当前租户的数字员工列表。
 *
 * 同一 tenantId + userId 的多个消费者共享同一份查询缓存；缓存策略统一由
 * QueryClient 的默认值提供（staleTime 30s、不重试），此处不再重复声明。
 * userId 进入 queryKey，确保切换登录账号后不会读到上一个账号的花名册。
 */
export function useAgents(tenantId: string, userId: string) {
  return useQuery({
    queryKey: ['agents', tenantId, userId],
    queryFn: () =>
      api.get<AgentProfileRead[]>(
        `/api/enterprise/agents?tenant_id=${tenantId}`,
      ),
  });
}
