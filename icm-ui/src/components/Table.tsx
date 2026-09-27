import type { ReactNode } from "react";
import { cx } from "./ui/cx";

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return <th className={className}>{children}</th>;
}

export function Td({ children, mono, num, className }: {
  children?: ReactNode; mono?: boolean; num?: boolean; className?: string;
}) {
  return (
    <td className={cx(
      "whitespace-nowrap",
      mono && "font-mono text-[12.5px]",
      num && "num text-right",
      className,
    )}>
      {children}
    </td>
  );
}
