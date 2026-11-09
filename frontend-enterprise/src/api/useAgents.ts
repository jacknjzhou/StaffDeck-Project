import { useQuery } from '@tanstack/react-query';

import type { AgentProfileRead } from '../types';
import { api } from './client';

/**
 * 读取当前租户的数字员工列表。
 *
 * 同一 tenantId 的多个消费者共享同一份查询缓存；staleTime 内不会重复请求，
 * 失败不重试，避免一次登录态异常引发请求风暴。
 */
export function useAgents(tenantId: string) {
  return useQuery({
    queryKey: ['agents', tenantId],
    queryFn: () =>
      api.get<AgentProfileRead[]>(
        `/api/enterprise/agents?tenant_id=${tenantId}`,
      ),
    staleTime: 30_000,
    retry: false,
  });
}
