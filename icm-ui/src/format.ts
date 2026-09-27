const TWO_DP = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const;

/** An amount for display: grouped, two decimals. */
export function money(value: string | number): string {
  const n = typeof value === "number" ? value : parseFloat(value);
  return Number.isFinite(n) ? n.toLocaleString(undefined, TWO_DP) : String(value);
}

/** "GBP 1,000.00": one currency per figure, never a mixed total. */
export function withCurrency(currency: string | undefined, value: string | number): string {
  return currency ? `${currency} ${money(value)}` : money(value);
}

/** Sum decimal strings in integer cents, so a column of pennies does not drift. */
export function sumCents(values: string[]): number {
  return values.reduce((total, v) => total + Math.round(parseFloat(v || "0") * 100), 0) / 100;
}

/** A percentage in the reader's locale: 1.167 -> "116.7%" (or "116,7 %"). */
export function percent(fraction: number, digits = 1): string {
  return new Intl.NumberFormat(undefined, {
    style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(fraction);
}
