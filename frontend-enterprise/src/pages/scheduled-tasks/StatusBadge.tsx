import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { BADGE_TONE_CLASS, RUN_STATUS_BADGE, TASK_STATUS_BADGE, type BadgeTone } from './shared';

/** 状态徽标基础组件：按 `tone` 套用配色，渲染圆角胶囊内的文本。 */
export function StatusBadge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-[12px] py-[4px] text-[10px] leading-none whitespace-nowrap capitalize',
        BADGE_TONE_CLASS[tone],
      )}
    >
      {children}
    </span>
  );
}

/**
 * 任务状态徽标：按 `status` 从 TASK_STATUS_BADGE 取文案与配色。
 *
 * 未知状态回退到 `archived` 预设，避免出现无样式的裸文本。
 */
export function TaskStatusBadge({ status }: { status: string }) {
  const { tone, text } = TASK_STATUS_BADGE[status] || TASK_STATUS_BADGE.archived;
  return <StatusBadge tone={tone}>{text}</StatusBadge>;
}

/**
 * 任务运行结果徽标：按 `status` 从 RUN_STATUS_BADGE 取文案与配色。
 *
 * 未知状态回退为灰色 tone，并直接展示原始状态文本（空值显示「暂无」）。
 */
export function TaskRunResultBadge({ status }: { status: string }) {
  const preset = RUN_STATUS_BADGE[status] || { tone: 'gray' as BadgeTone, text: status || '暂无' };
  return <StatusBadge tone={preset.tone}>{preset.text}</StatusBadge>;
}
