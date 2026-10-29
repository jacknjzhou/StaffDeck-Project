import { describe, expect, it } from 'vitest';

import type { FeedbackSessionRead } from '@/types';

import {
  buildConversationUserOptions,
  matchesConversationLogFilter,
  type ConversationLogRow,
} from './conversationLogFilters';

/** 构造一条会话反馈记录，可按需指定主要问题分类。 */
function feedback(
  sessionId: string,
  primaryBucket?: string,
): FeedbackSessionRead {
  return {
    session_id: sessionId,
    tenant_id: 'tenant-demo',
    status: 'completed',
    feedback_count: 1,
    latest_feedback_at: '2026-08-02T00:00:00Z',
    latest_message_id: `message-${sessionId}`,
    latest_message: 'feedback',
    primary_bucket: primaryBucket,
    updated_at: '2026-08-02T00:00:00Z',
  };
}

/** 构造一行会话日志，并按需覆盖已有字段。 */
function row(
  id: string,
  userId: string,
  displayName: string,
  patch: Partial<ConversationLogRow> = {},
): ConversationLogRow {
  return {
    id,
    tenant_id: 'tenant-demo',
    user_id: userId,
    status: 'completed',
    session_display_name: displayName,
    session_username: `${displayName.toLowerCase()}-user`,
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
    ...patch,
  };
}

/** 验证会话日志的用户选项与分类筛选匹配规则。 */
describe('conversation log filters', () => {
  /** 验证用户选项只统计当前日志分类中出现的用户。 */
  it('counts only users present in the current log category', () => {
    const rows = [
      row('session-a-up', 'user-a', 'Alice', { upFeedback: feedback('session-a-up') }),
      row('session-a-down', 'user-a', 'Alice', { downFeedback: feedback('session-a-down') }),
      row('session-b-up', 'user-b', 'Bob', { upFeedback: feedback('session-b-up') }),
    ];

    expect(buildConversationUserOptions(rows)).toEqual([
      { userId: 'user-a', label: 'Alice · @alice-user', count: 2 },
      { userId: 'user-b', label: 'Bob · @bob-user', count: 1 },
    ]);

    const downRows = rows.filter((item) => matchesConversationLogFilter(item, 'down'));
    expect(buildConversationUserOptions(downRows)).toEqual([
      { userId: 'user-a', label: 'Alice · @alice-user', count: 1 },
    ]);
  });

  /** 验证问题标签映射到反馈分类并忽略缺失用户 ID 的记录。 */
  it('maps issue tabs to their feedback buckets and omits missing user ids', () => {
    const abilityRow = row('session-ability', 'user-a', 'Alice', {
      downFeedback: feedback('session-ability', 'model_issue'),
    });
    const knowledgeRow = row('session-knowledge', 'user-b', 'Bob', {
      downFeedback: feedback('session-knowledge', 'unknown'),
    });
    const anonymousRow = row('session-anonymous', '', 'Unknown', {
      downFeedback: feedback('session-anonymous', 'model_issue'),
    });

    expect(matchesConversationLogFilter(abilityRow, 'ability')).toBe(true);
    expect(matchesConversationLogFilter(abilityRow, 'knowledge')).toBe(false);
    expect(matchesConversationLogFilter(knowledgeRow, 'knowledge')).toBe(true);
    expect(buildConversationUserOptions([anonymousRow])).toEqual([]);
  });
});
