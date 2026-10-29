import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export type DetailFieldProps = {
  label: string;
  children: ReactNode;
  className?: string;
};

/**
 * 详情弹窗（员工记忆 / 对话日志 / SOP 版本详情 等）中使用的带标签只读字段：
 * 一张带边框的浅色卡片，包含 11px 的标题文字与其取值。
 */
export function DetailField({ label, children, className }: DetailFieldProps) {
  return (
    <div
      className={cn(
        'flex min-w-0 flex-col gap-[6px] rounded-[10px] border border-[#eef0f4] bg-[#fafbfc] px-[12px] py-[10px]',
        className,
      )}
    >
      <span className="text-[11px] font-semibold text-[#858b9c]">{label}</span>
      <div className="min-w-0 wrap-break-word text-[12px] text-[#18181a]">{children}</div>
    </div>
  );
}
