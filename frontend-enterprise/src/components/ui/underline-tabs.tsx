import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export type UnderlineTabItem<T extends string = string> = {
  value: T;
  label: ReactNode;
  disabled?: boolean;
};

/**
 * `dot`  – 激活标签下方居中的短圆角指示条（SD1 节点 38:6404）。
 * `line` – 整宽底部分割线之上、与标签等宽的 2px 激活线（SD1 节点 281:1935）。
 */
export type UnderlineTabsVariant = 'dot' | 'line';

export type UnderlineTabsProps<T extends string = string> = {
  items: UnderlineTabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  variant?: UnderlineTabsVariant;
  className?: string;
  /** 每个标签按钮的额外类名（例如覆盖默认的固定宽度）。 */
  tabClassName?: string;
  'aria-label'?: string;
};

/**
 * 全局下划线标签栏。
 *
 * - 受控组件：选中项完全由 `value` 决定，点击非禁用标签时回调 `onChange`。
 * - 默认 `variant="dot"` 渲染短圆角指示条，`variant="line"` 渲染整宽分割线与等宽激活线。
 * - 标签宽度默认固定 120px，可用 `tabClassName` 覆盖；禁用项不触发回调。
 */
export function UnderlineTabs<T extends string = string>({
  items,
  value,
  onChange,
  variant = 'dot',
  className,
  tabClassName,
  'aria-label': ariaLabel,
}: UnderlineTabsProps<T>) {
  const isLine = variant === 'line';
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        'flex items-start',
        isLine && 'border-b-[0.5px] border-[#e3e7f1]',
        className,
      )}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={item.disabled}
            onClick={() => onChange(item.value)}
            className={cn(
              'relative flex w-[120px] items-start justify-center px-[16px] text-[14px] capitalize transition-colors outline-none',
              isLine ? 'pt-[6px] pb-[8px] mb-[-0.5px] border-b-2' : 'py-[6px]',
              isLine
                ? active
                  ? 'border-[#18181a] font-medium text-[#18181a]'
                  : 'border-transparent font-normal text-[#4f5669] hover:text-[#18181a]'
                : active
                  ? 'font-medium text-[#18181A]'
                  : 'font-normal text-[#858B9C] hover:text-[#18181A]',
              'disabled:cursor-not-allowed disabled:opacity-50',
              tabClassName,
            )}
          >
            {item.label}
            {!isLine && active && (
              <span
                aria-hidden="true"
                className="absolute top-[33px] left-1/2 h-[3px] w-[10px] -translate-x-1/2 rounded-[4px] bg-[#18181A] max-[560px]:top-auto max-[560px]:bottom-0"
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
