import type { ReactNode } from 'react';
import { AlertDialog as AlertDialogPrimitive } from 'radix-ui';

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from '@/components/ui';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import IconWarningFill from '../assets/icons/warning-fill.svg?react';

export type ConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 头部标题，支持富内容（例如用 `<strong>` 包裹目标名称）。 */
  title: ReactNode;
  /** 可选的补充说明，展示在标题下方。 */
  description?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  /** 为 true 时按钮禁用，并禁止通过遮罩或 ESC 关闭。 */
  loading?: boolean;
  /** 确认按钮是否使用危险（红色）样式，默认 true，与删除流程一致。 */
  destructive?: boolean;
  /** 覆盖头部前置图标；传入 `null` 可隐藏图标。 */
  icon?: ReactNode;
};

/**
 * 删除/危险操作确认弹窗（对齐 SD1 设计稿 node 281:5153）。
 *
 * - 结构：警示图标 + 标题头部、灰色说明文案，以及右对齐的「取消 / 确认」底部按钮。
 * - 基于 Radix `AlertDialog` 原语实现，焦点捕获与无障碍语义由其负责。
 * - `loading` 为 true 时按钮禁用，并拦截遮罩/ESC 触发的关闭。
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = '删除',
  cancelText = '取消',
  onConfirm,
  loading = false,
  destructive = true,
  icon,
}: ConfirmDialogProps) {
  const leadingIcon =
    icon === undefined ? (
      <IconWarningFill className="mt-px size-[16px] shrink-0 text-[#ff7f00]" />
    ) : (
      icon
    );

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (loading && !next) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent className="gap-0 overflow-hidden rounded-[16px] p-0">
        <div className="flex items-start gap-[8px] px-[16px] pt-[16px] pb-[12px]">
          {leadingIcon}
          <AlertDialogTitle className="min-w-0 flex-1 text-[14px] leading-[normal] font-medium text-[#18181a] [word-break:break-word]">
            {title}
          </AlertDialogTitle>
        </div>
        {description != null && (
          <div className="px-[24px] pb-[12px]">
            <AlertDialogDescription className="text-[14px] leading-[20px] text-[#4f5669] [word-break:break-word]">
              {description}
            </AlertDialogDescription>
          </div>
        )}
        <div className="flex items-center justify-end gap-[8px] pt-[12px] pr-[16px] pb-[16px] pl-[12px]">
          <AlertDialogPrimitive.Cancel asChild>
            <Button
              variant="outline"
              disabled={loading}
              className="h-[32px] w-[80px] rounded-[10px] border-[#e3e7f1] bg-white px-[12px] py-[8px] text-[14px] font-normal text-[#464c5e] hover:border-[#e3e7f1] hover:bg-[#f6f6f6] hover:text-[#18181a]"
            >
              {cancelText}
            </Button>
          </AlertDialogPrimitive.Cancel>
          <AlertDialogPrimitive.Action asChild>
            <Button
              disabled={loading}
              className={cn(
                'h-[32px] w-[80px] rounded-[10px] px-[12px] py-[8px] text-[14px] font-normal',
                destructive
                  ? 'bg-[#d20b0b] text-white hover:bg-[#b80909]'
                  : 'bg-[#18181a] text-white hover:bg-[#303030]',
              )}
              onClick={(event) => {
                event.preventDefault();
                onConfirm();
              }}
            >
              {confirmText}
            </Button>
          </AlertDialogPrimitive.Action>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
