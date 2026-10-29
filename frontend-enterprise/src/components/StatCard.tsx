import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/** 指标卡配色风格：中性灰、绿色（正向）或红色（负向）。 */
export type StatCardTone = 'default' | 'green' | 'red';

/** 各配色风格对应的卡片背景类名。 */
const SURFACE_CLASS: Record<StatCardTone, string> = {
  default: 'bg-[#f6f6f6]',
  green: 'bg-[#e9f7ef]',
  red: 'bg-[#fce7e7]',
};
/** 各配色风格对应的大号数值文字类名。 */
const VALUE_CLASS: Record<StatCardTone, string> = {
  default: 'text-[#18181a]',
  green: 'text-[#2cb360]',
  red: 'text-[#d20b0b]',
};
/** 各配色风格对应的说明文字类名。 */
const LABEL_CLASS: Record<StatCardTone, string> = {
  default: 'text-[#464c5e]',
  green: 'text-[#2cb360]',
  red: 'text-[#d20b0b]',
};

/** 指标卡属性：数值、说明文案与可选样式覆盖。 */
export type StatCardProps = {
  value: ReactNode;
  label: ReactNode;
  /** 配色风格：`default` 为中性灰卡片，`green` / `red` 为对应色调。 */
  tone?: StatCardTone;
  /** 大号数值的附加类名（例如自定义颜色）。 */
  valueClassName?: string;
  /** 外层卡片的附加类名（例如覆盖 flex 基准宽度）。 */
  className?: string;
};

/**
 * 企业各页面（定时任务 / 对话日志 / 技能……）复用的指标卡：圆角色块容器内展示
 * 大号数值与紧随其后的说明文字。
 */
export function StatCard({ value, label, tone = 'default', valueClassName, className }: StatCardProps) {
  return (
    <div
      className={cn(
        'flex h-[70px] flex-1 basis-[180px] items-center rounded-[14px] px-[24px] py-[8px]',
        SURFACE_CLASS[tone],
        className,
      )}
    >
      <div className="flex min-w-0 items-baseline gap-[6px]">
        <span className={cn('shrink-0 text-[26px] font-semibold leading-none', VALUE_CLASS[tone], valueClassName)}>
          {value}
        </span>
        <span className={cn('truncate text-[14px] leading-none', LABEL_CLASS[tone])}>{label}</span>
      </div>
    </div>
  );
}
