// Display only: amounts arrive as exact decimal strings from qp; formatting never changes
// the value used anywhere else.
const tl = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(amount: string | null | undefined, currency = "TRY"): string {
  if (amount === null || amount === undefined) return "—";
  const n = Number(amount);
  const sign = currency === "TRY" ? "₺" : currency === "USD" ? "$" : `${currency} `;
  return `${n < 0 ? "−" : ""}${sign}${tl.format(Math.abs(n))}`;
}

export function pct(fraction: string | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined) return "—";
  return `${(Number(fraction) * 100).toFixed(digits)}%`;
}

/** "Expenses:Food:Groceries" -> "Groceries"; with group -> "Food · Groceries". */
export function label(account: string, withGroup = false): string {
  const parts = account.split(":").slice(1);
  if (parts.length === 0) return account;
  const nice = parts.map((p) => p.replace(/-/g, " "));
  return withGroup ? nice.join(" · ") : nice[nice.length - 1];
}

export function day(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
  });
}
