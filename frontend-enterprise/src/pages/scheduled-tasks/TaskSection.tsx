import type { ReactNode } from 'react';

import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { Paginator } from '@/components/Paginator';
import { UnderlineTabs, type UnderlineTabItem } from '@/components/ui';

export type TaskSectionProps<TFilter extends string, TRow> = {
  /** 头部前置图标（14px）。 */
  icon: ReactNode;
  title: string;
  filterTabs: UnderlineTabItem<TFilter>[];
  filter: TFilter;
  onFilterChange: (value: TFilter) => void;
  /** 全部筛选后的行数据——决定移动端列表与分页器是否展示。 */
  rows: TRow[];
  /** 当前页的行数据——供桌面端表格渲染。 */
  pagedRows: TRow[];
  columns: DataTableColumn<TRow>[];
  rowKey: (row: TRow, index: number) => string | number;
  loading?: boolean;
  emptyText: string;
  tableSize?: 'default' | 'compact';
  striped?: boolean;
  bordered?: boolean;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  /** 移动端（<768px）单行卡片渲染函数。 */
  renderMobileCard: (row: TRow) => ReactNode;
};

/**
 * 定时任务页面使用的带标题列表区块：头部（图标 + 标题）、下划线筛选标签栏、
 * 移动端卡片列表，以及桌面端 `DataTable` + `Paginator` 组合。任务列表与执行日志均复用。
 *
 * - 移动端渲染全部 `rows`，桌面端仅渲染 `pagedRows`，分页由父组件控制。
 * - 无数据时显示 `emptyText`，且 `rows` 为空时隐藏分页器。
 */
export function TaskSection<TFilter extends string, TRow>({
  icon,
  title,
  filterTabs,
  filter,
  onFilterChange,
  rows,
  pagedRows,
  columns,
  rowKey,
  loading,
  emptyText,
  tableSize = 'default',
  striped = false,
  bordered = false,
  page,
  pageCount,
  onPageChange,
  renderMobileCard,
}: TaskSectionProps<TFilter, TRow>) {
  return (
    <section aria-label={title}>
      <div className="mb-[16px] flex items-center gap-[6px] px-[12px] text-[#757f9c]">
        {icon}
        <span className="text-[14px] font-normal leading-none">{title}</span>
      </div>
      <UnderlineTabs
        aria-label={`${title}筛选`}
        variant="line"
        className="mb-[16px]"
        value={filter}
        onChange={onFilterChange}
        items={filterTabs}
      />
      <div className="grid gap-[10px] md:hidden">
        {rows.length ? (
          rows.map(renderMobileCard)
        ) : (
          <div className="py-[40px] text-center text-[13px] text-[#858b9c]">{emptyText}</div>
        )}
      </div>
      <div className="hidden md:block">
        <DataTable
          aria-label={title}
          columns={columns}
          data={pagedRows}
          rowKey={rowKey}
          loading={loading}
          emptyText={emptyText}
          size={tableSize}
          striped={striped}
          bordered={bordered}
        />
        {rows.length > 0 && (
          <Paginator
            aria-label={`${title}分页`}
            page={page}
            pageCount={pageCount}
            onChange={onPageChange}
          />
        )}
      </div>
    </section>
  );
}
