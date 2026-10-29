import type { EnterpriseChatSessionRead, FeedbackSummaryRead } from '../../types';

export type EmployeeDashboardMetrics = {
  conversationCount: number;
  feedbackCount: number;
  positiveRate: number;
  negativeRate: number;
};

/**
 * 根据会话列表与反馈汇总计算员工仪表盘指标。
 *
 * - 返回语义：会话总数、反馈总数，以及正向/负向反馈占比（四舍五入取整的百分比）。
 * - 边界条件：summary 为空或反馈总数为 0 时，反馈数记为 0，正负向比例均返回 0，避免除零。
 */
export function employeeDashboardMetrics(
  sessions: EnterpriseChatSessionRead[],
  summary: FeedbackSummaryRead | null,
): EmployeeDashboardMetrics {
  const feedbackCount = summary?.total_feedback ?? 0;
  return {
    conversationCount: sessions.length,
    feedbackCount,
    positiveRate: feedbackCount ? Math.round(((summary?.up_count ?? 0) / feedbackCount) * 100) : 0,
    negativeRate: feedbackCount ? Math.round(((summary?.down_count ?? 0) / feedbackCount) * 100) : 0,
  };
}
