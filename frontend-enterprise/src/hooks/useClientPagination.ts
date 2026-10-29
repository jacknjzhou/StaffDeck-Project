import { useEffect, useMemo, useState } from 'react';

export type ClientPagination<T> = {
  /** 当前页码（从 1 开始）。 */
  page: number;
  setPage: (page: number) => void;
  /** 总页数（最小为 1）。 */
  pageCount: number;
  /** 当前页包含的条目。 */
  pagedItems: T[];
};

/**
 * 对内存列表做客户端分页的 Hook。
 *
 * 列表变短时会把当前页收敛到有效范围内，`resetKey` 变化（例如切换筛选条件）时重置回第 1 页。
 */
export function useClientPagination<T>(
  items: T[],
  pageSize: number,
  resetKey?: unknown,
): ClientPagination<T> {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  useEffect(() => {
    setPage((prev) => Math.min(prev, pageCount));
  }, [pageCount]);

  useEffect(() => {
    setPage(1);
  }, [resetKey]);

  const pagedItems = useMemo(
    () => items.slice((page - 1) * pageSize, page * pageSize),
    [items, page, pageSize],
  );

  return { page, setPage, pageCount, pagedItems };
}
