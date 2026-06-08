import { useMemo, useState } from "react";

/**
 * Generic hook for sortable, filterable, paginated tables.
 * Works with any array of objects — uses String() for value extraction.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useTableSort(data: any[], defaultSort = "id", pageSize = 20) {
  const [sortCol, setSortCol] = useState(defaultSort);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);

  const toggleSort = (col: string) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
  };

  const setFilter = (col: string, value: string) => {
    setFilters(prev => {
      const next = { ...prev };
      if (value) next[col] = value.toLowerCase();
      else delete next[col];
      return next;
    });
    setPage(1); // reset to first page on filter change
  };

  const clearFilters = () => { setFilters({}); setPage(1); };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result: any[] = useMemo(() => {
    let list = [...data];
    for (const [col, val] of Object.entries(filters)) {
      list = list.filter(item =>
        String(item[col] ?? "").toLowerCase().includes(val),
      );
    }
    list.sort((a, b) => {
      const av = String(a[sortCol] ?? "").toLowerCase();
      const bv = String(b[sortCol] ?? "").toLowerCase();
      const isNum = !isNaN(parseFloat(av)) && !isNaN(parseFloat(bv)) && av !== "" && bv !== "";
      if (isNum) {
        return sortDir === "asc" ? parseFloat(av) - parseFloat(bv) : parseFloat(bv) - parseFloat(av);
      }
      return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return list;
  }, [data, sortCol, sortDir, filters]);

  const totalPages = Math.max(1, Math.ceil(result.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const paginated = result.slice((safePage - 1) * pageSize, safePage * pageSize);

  return {
    result,
    paginated,
    totalItems: result.length,
    page: safePage,
    totalPages,
    pageSize,
    setPage,
    sortCol, sortDir, filters,
    toggleSort, setFilter, clearFilters,
  };
}
