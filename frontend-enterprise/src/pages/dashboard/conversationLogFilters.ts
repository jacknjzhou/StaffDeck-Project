import type {
  EnterpriseChatSessionRead,
  FeedbackSessionRead,
} from '@/types';

export type ConversationLogFilter =
  | 'all'
  | 'up'
  | 'down'
  | 'unrated'
  | 'ability'
  | 'tool'
  | 'knowledge'
  | 'sop';

export type ConversationLogRow = EnterpriseChatSessionRead & {
  downFeedback?: FeedbackSessionRead;
  upFeedback?: FeedbackSessionRead;
};

export type ConversationUserOption = {
  userId: string;
  label: string;
  count: number;
};

/**
 * 判断单条会话日志是否命中当前筛选条件。
 *
 * - 'all' 恒为真，'up'/'down'/'unrated' 依据是否存在对应反馈；
 * - 其余分类读取 downFeedback.primary_bucket 与问题类型对应。
 * @param row 会话日志行，含可选的正/负反馈对象。
 * @param filter 当前选中的筛选枚举值。
 * @returns 命中返回 true，否则返回 false。
 */
export function matchesConversationLogFilter(
  row: ConversationLogRow,
  filter: ConversationLogFilter,
): boolean {
  if (filter === 'up') return Boolean(row.upFeedback);
  if (filter === 'down') return Boolean(row.downFeedback);
  if (filter === 'unrated') return !row.upFeedback && !row.downFeedback;
  if (filter === 'ability') return row.downFeedback?.primary_bucket === 'model_issue';
  if (filter === 'tool') return row.downFeedback?.primary_bucket === 'tool_or_system_issue';
  if (filter === 'sop') return row.downFeedback?.primary_bucket === 'skill_issue';
  if (filter === 'knowledge') return row.downFeedback?.primary_bucket === 'unknown';
  return true;
}

/**
 * 从会话日志中汇总用户筛选项：按 user_id 去重，累计出现次数，忽略无用户 ID 的行。
 *
 * @param rows 待汇总的会话日志行集合。
 * @returns 按中文标签升序排列的用户选项数组。
 */
export function buildConversationUserOptions(
  rows: readonly ConversationLogRow[],
): ConversationUserOption[] {
  const options = new Map<string, ConversationUserOption>();
  rows.forEach((row) => {
    const userId = String(row.user_id || '').trim();
    if (!userId) return;
    const current = options.get(userId);
    options.set(userId, {
      userId,
      label: conversationUserLabel(row),
      count: (current?.count || 0) + 1,
    });
  });
  return Array.from(options.values()).sort((left, right) => (
    left.label.localeCompare(right.label, 'zh-CN')
  ));
}

/**
 * 生成用户展示标签：显示名与用户名不同时拼接为“显示名 · @用户名”。
 *
 * @param session 会话记录，读取 session_display_name 与 session_username。
 * @returns 拼接后的标签；两者皆空时返回“未知用户”。
 */
function conversationUserLabel(session: EnterpriseChatSessionRead): string {
  const displayName = String(session.session_display_name || '').trim();
  const username = String(session.session_username || '').trim();
  if (displayName && username && displayName !== username) {
    return `${displayName} · ${username.startsWith('@') ? username : `@${username}`}`;
  }
  return displayName || username || '未知用户';
}
