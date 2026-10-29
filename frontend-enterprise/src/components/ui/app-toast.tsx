import type { ReactNode } from 'react';
import { toast, type ExternalToast } from 'sonner';

import { apiErrorMessage } from '@/lib/apiErrorMessages';
import { cn } from '@/lib/utils';

import IconError from '@/assets/icons/error-fill.svg?react';
import IconSuccess from '@/assets/icons/success-fill.svg?react';

type ToastVariant = 'success' | 'error';

// Colors, radius and spacing mirror SD1 "Basic components/Dialog/Message"
// (success node 281:3334, error node 281:3342).
const VARIANTS: Record<
  ToastVariant,
  { container: string; icon: string; Icon: typeof IconSuccess }
> = {
  success: {
    container: 'border-[#96d9b0] bg-[#e9f7ef] text-[#018434]',
    icon: 'text-[#2cb360]',
    Icon: IconSuccess,
  },
  error: {
    container: 'border-[#f38989] bg-[#fce7e7] text-[#d20b0b]',
    icon: 'text-[#d20b0b]',
    Icon: IconError,
  },
};

/**
 * 渲染单条品牌化提示气泡，按变体切换配色与图标。
 *
 * - 使用 role="status" 与 aria-live="polite" 保证读屏器播报。
 * - 纯展示组件，不负责自动关闭时长与屏幕定位。
 */
function ToastPill({ variant, message }: { variant: ToastVariant; message: ReactNode }) {
  const { container, icon, Icon } = VARIANTS[variant];
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'pointer-events-auto flex max-w-full items-center gap-[12px] rounded-[14px] border border-solid px-[24px] py-[10px] shadow-[0px_12px_32px_rgba(0,0,0,0.12)]',
        container,
      )}
    >
      <Icon className={cn('size-[16px] shrink-0', icon)} aria-hidden="true" />
      <span className="text-[14px] leading-[normal] wrap-anywhere">{message}</span>
    </div>
  );
}

/**
 * 品牌 toast 接受的配置项。
 *
 * 图标、样式与居中定位由组件自身负责，因此这些键从 options 中排除。
 */
export type AppToastOptions = Omit<
  ExternalToast,
  'icon' | 'className' | 'style' | 'unstyled' | 'descriptionClassName'
>;

/**
 * 按变体渲染自定义 toast，并套用统一时长与居中外层容器。
 *
 * @param variant 展示变体，决定配色、图标与默认停留时长。
 * @param message 提示内容，可为字符串或任意 React 节点。
 * @param options 透传给 sonner 的额外配置，可覆盖默认时长等字段。
 * @returns sonner 返回的 toast id，可用于后续 dismiss。
 */
function showVariant(variant: ToastVariant, message: ReactNode, options?: AppToastOptions) {
  return toast.custom(() => <ToastPill variant={variant} message={message} />, {
    duration: variant === 'success' ? 3200 : 4800,
    unstyled: true,
    className: 'flex w-full justify-center',
    ...options,
  });
}

/**
 * 把后端错误码或英文原文转换为当前界面语言的提示文案。
 *
 * @param message 原始提示；非字符串时原样返回。
 * @returns 本地化后的提示，找不到映射时回退为原文。
 */
function localizedErrorMessage(message: ReactNode): ReactNode {
  if (typeof message !== 'string') return message;
  return apiErrorMessage(message, message);
}

/**
 * 全局 toast 入口。
 *
 * - `success` / `error` 渲染 SD1 消息气泡，`error` 会先做错误文案本地化。
 * - `warning` / `info` / `loading` 直接委托 sonner，共享 <Toaster /> 的居中定位。
 * - `dismiss` 关闭指定 id 的提示，省略 id 时关闭全部。
 */
export const notify = {
  success: (message: ReactNode, options?: AppToastOptions) =>
    showVariant('success', message, options),
  error: (message: ReactNode, options?: AppToastOptions) =>
    showVariant('error', localizedErrorMessage(message), options),
  warning: (message: ReactNode, options?: AppToastOptions) => toast.warning(message, options),
  info: (message: ReactNode, options?: AppToastOptions) => toast.info(message, options),
  loading: (message: ReactNode, options?: AppToastOptions) => toast.loading(message, options),
  dismiss: (id?: string | number) => toast.dismiss(id),
};
