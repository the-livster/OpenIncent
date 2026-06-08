import { useEffect, useState } from "react";

/**
 * Debounce a value by `delay` ms.
 * Returns the debounced value which only updates after the input stops changing
 * for the specified delay period.
 */
export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}
