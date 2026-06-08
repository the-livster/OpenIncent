import type { ReactNode } from "react";

export function Th({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <th className={`px-3 py-2 text-left font-medium text-ink2 whitespace-nowrap ${className ?? ""}`}>
      {children}
    </th>
  );
}

export function Td({ children, mono, className }: { children: ReactNode; mono?: boolean; className?: string }) {
  return (
    <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-ink" : "text-ink"} ${className ?? ""}`}>
      {children}
    </td>
  );
}
