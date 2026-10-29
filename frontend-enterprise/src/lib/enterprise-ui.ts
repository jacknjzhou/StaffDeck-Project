import { formatClientDateTime } from './timezone';

/**
 * 企业列表页（SOP、技能、定时任务、员工记忆、对话日志……）共用的 Tailwind 类名常量。
 *
 * 集中维护可避免在各页面重复粘贴相同的下拉菜单、选择器与卡片样式。
 */

/** 下拉菜单项（图标 + 文字，12px 次要色文本）。 */
export const MENU_ITEM_CLASS =
  'cursor-pointer gap-[6px] rounded-[10px] px-[12px] py-[6px] text-[12px] text-[#858b9c] focus:text-[#18181a] [&_svg]:size-[14px]';

/** 危险操作（红色）下拉菜单项。 */
export const MENU_ITEM_DANGER_CLASS =
  'cursor-pointer gap-[6px] rounded-[10px] px-[12px] py-[6px] text-[12px] text-[#d20b0b] focus:bg-[#fce7e7] focus:text-[#d20b0b] focus:[&_svg]:text-[#d20b0b]! [&_svg]:size-[14px]';

/** 下拉菜单浮层容器（圆角白色卡片 + 柔和阴影）。 */
export const MENU_CONTENT_CLASS =
  'flex w-auto min-w-[140px] flex-col gap-[4px] rounded-[14px] border-0 bg-white p-[4px] shadow-[0px_0px_8px_rgba(0,0,0,0.1)] ring-0 [--accent:#F6F6F6] [--accent-foreground:#18181A]';

/** shadcn `Select` 触发器，样式与 34px 高的筛选控件对齐。 */
export const SELECT_TRIGGER_CLASS =
  'h-[34px] data-[size=default]:h-[34px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white text-[12px] text-[#464c5e] shadow-none data-placeholder:text-[#858b9c] hover:border-[#cbd3e6] focus-visible:border-[#18181a] focus-visible:ring-0';

/** 移动端（<768px）列表卡片容器。 */
export const MOBILE_CARD_CLASS =
  'min-w-0 rounded-[8px] border border-[#eceef1] bg-white p-[14px]';

/** 对话框底部栏：白色背景、顶部边框、操作按钮右对齐。 */
export const DIALOG_FOOTER_CLASS =
  'flex items-center justify-end gap-[8px] bg-white px-[24px] py-[12px]';

/** 标准对话框取消按钮。 */
export const DIALOG_CANCEL_BUTTON_CLASS =
  'h-[32px] min-w-[80px] rounded-[10px] border-[#e3e7f1] bg-white px-[12px] text-[14px] font-normal text-[#464c5e] hover:border-[#e3e7f1] hover:bg-[#f6f6f6] hover:text-[#18181a]';

/** 标准对话框主要确认按钮。 */
export const DIALOG_PRIMARY_BUTTON_CLASS =
  'h-[32px] min-w-[80px] rounded-[10px] bg-[#18181a] px-[12px] text-[14px] font-normal text-white hover:bg-[#303030]';

/** 标准描边操作按钮（工具栏刷新、卡片操作等）。 */
export const OUTLINE_ACTION_BUTTON_CLASS =
  'h-[34px] gap-[4px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[20px] text-[12px] font-normal text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a]';

/** 卡片内联头部使用的紧凑描边操作按钮。 */
export const OUTLINE_ACTION_BUTTON_SM_CLASS =
  'h-[32px] gap-[4px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[12px] text-[12px] font-normal text-[#464c5e] hover:border-[#cbd3e6] hover:bg-[#f6f6f6] hover:text-[#18181a] [&_svg:not([class*="size-"])]:size-[14px]';

/** 组合式搜索框容器（输入框 + 提交按钮）。 */
export const SEARCH_COMBO_CLASS =
  'flex h-[32px] min-w-0 items-stretch overflow-hidden rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white transition-colors focus-within:border-[#18181a]';

/** 组合式搜索框输入框。 */
export const SEARCH_COMBO_INPUT_CLASS =
  'min-w-0 flex-1 bg-transparent px-[14px] text-[14px] text-[#17191f] outline-none placeholder:text-[#c0c6d4]';

/** 组合式搜索框提交按钮。 */
export const SEARCH_COMBO_BUTTON_CLASS =
  'shrink-0 bg-[#18181a] px-[20px] text-[14px] font-normal text-white transition-colors hover:bg-[#303030] disabled:pointer-events-none disabled:opacity-50';

/**
 * 按当前 UI 语言格式化后端时间戳。
 *
 * @param value 后端返回的时间字符串；为空或非法时返回 `-`。
 * @returns 本地化后的日期时间文本，缺省为 `-`。
 */
export function formatDateTime(value?: string): string {
  return formatClientDateTime(value, '-');
}
