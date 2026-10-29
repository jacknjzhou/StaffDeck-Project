import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react';

import { cn } from '@/lib/utils';

import { Pagination, PaginationContent, PaginationItem } from './ui';

export type PaginatorProps = {
  /** 当前页码，从 1 开始。 */
  page: number;
  /** 总页数。 */
  pageCount: number;
  onChange: (page: number) => void;
  /** 当前页左右两侧各显示的页码数量。 */
  siblingCount?: number;
  /** 页码是否补零（01、02……）以匹配 SD1 设计，默认 true。 */
  padZero?: boolean;
  className?: string;
  'aria-label'?: string;
};

const PILL_CLASS =
  'flex h-[20px] min-w-[20px] items-center justify-center rounded-[6px] px-[12px] text-[10px] leading-none transition-colors';
const ARROW_CLASS =
  'flex size-[14px] shrink-0 items-center justify-center text-[#464c5e] transition-opacity disabled:cursor-not-allowed disabled:opacity-30';

/**
 * 计算分页条要展示的页码序列。
 *
 * 页数不超过 `siblingCount * 2 + 5` 时直接列出全部页码；
 * 否则固定保留首尾页，中间超出可视窗口的部分用 'ellipsis' 占位。
 */
function getPaginationRange(
  current: number,
  total: number,
  siblingCount: number,
): (number | 'ellipsis')[] {
  const totalNumbers = siblingCount * 2 + 5;
  if (total <= totalNumbers) {
    return Array.from({ length: total }, (_, index) => index + 1);
  }
  const items: (number | 'ellipsis')[] = [1];
  const start = Math.max(2, current - siblingCount);
  const end = Math.min(total - 1, current + siblingCount);
  if (start > 2) items.push('ellipsis');
  for (let page = start; page <= end; page += 1) items.push(page);
  if (end < total - 1) items.push('ellipsis');
  items.push(total);
  return items;
}

/**
 * 全局分页器（SD1 设计稿：node 281:2020）。
 *
 * 基于 shadcn 的 `Pagination` 原语（nav / ul / li）获得语义结构与可访问性，
 * 外观遵循 SD1：20px 药丸、10px 补零页码、`#f6f6f6` 选中底色、
 * `#999` 未选中文字以及 14px 箭头。
 */
export function Paginator({
  page,
  pageCount,
  onChange,
  siblingCount = 1,
  padZero = true,
  className,
  'aria-label': ariaLabel,
}: PaginatorProps) {
  if (pageCount < 1) return null;
  const range = getPaginationRange(page, pageCount, siblingCount);
  /** 按 `padZero` 决定是否把页码补成两位数字面量。 */
  const label = (value: number) => (padZero ? String(value).padStart(2, '0') : String(value));
  /** 把目标页码夹在 [1, pageCount] 内，仅在页码变化时触发 `onChange`。 */
  const goTo = (target: number) => {
    const next = Math.min(Math.max(target, 1), pageCount);
    if (next !== page) onChange(next);
  };
  return (
    <Pagination aria-label={ariaLabel} className={cn('mt-[16px]', className)}>
      <PaginationContent className="gap-[16px]">
        <PaginationItem>
          <button
            type="button"
            className={ARROW_CLASS}
            disabled={page <= 1}
            onClick={() => goTo(page - 1)}
            aria-label="上一页"
          >
            <ChevronLeftIcon className="size-[14px]" />
          </button>
        </PaginationItem>
        {range.map((item, index) =>
          item === 'ellipsis' ? (
            <PaginationItem key={`ellipsis-${index}`}>
              <span
                aria-hidden="true"
                className="flex h-[20px] items-center justify-center px-[4px] text-[10px] leading-none text-[#999]"
              >
                ···
              </span>
            </PaginationItem>
          ) : (
            <PaginationItem key={item}>
              <button
                type="button"
                aria-current={item === page ? 'page' : undefined}
                onClick={() => goTo(item)}
                className={cn(
                  PILL_CLASS,
                  item === page
                    ? 'bg-[#f6f6f6] text-[#464c5e]'
                    : 'text-[#999] hover:bg-[#f2f3f7] hover:text-[#464c5e]',
                )}
              >
                {label(item)}
              </button>
            </PaginationItem>
          ),
        )}
        <PaginationItem>
          <button
            type="button"
            className={ARROW_CLASS}
            disabled={page >= pageCount}
            onClick={() => goTo(page + 1)}
            aria-label="下一页"
          >
            <ChevronRightIcon className="size-[14px]" />
          </button>
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}
