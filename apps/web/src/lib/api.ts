// Client for the books JSON API (apps/api). Same origin in production (`make app`);
// in development set NEXT_PUBLIC_API=http://127.0.0.1:8765 and run `make app` alongside.
const BASE = process.env.NEXT_PUBLIC_API ?? "";
export const TOKEN_KEY = "qp-token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string, params?: Record<string, string | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params ?? {})) if (v) q.set(k, v);
    const qs = q.toString();
    return request<T>(qs ? `${path}?${qs}` : path);
  },
  post: <T,>(path: string, body: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  upload: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

// ---- response types (amounts are decimal strings; never parsed for arithmetic)

export type Tx = {
  id: string | null;
  date: string;
  payee: string | null;
  description: string;
  account: string;
  amount: string;
  currency: string;
  category: string;
  tags: string[];
  review: boolean;
  source: string;
};

export type BudgetLine = {
  account: string;
  period: string;
  budget: string;
  actual: string;
  remaining: string;
  used: string | null;
  status: "ok" | "warning" | "over" | "no budget";
};

export type Meta = {
  today: string;
  categories: string[];
  accounts: string[];
  sources: { name: string; account: string; ready: boolean; verified: boolean }[];
  errors: string[];
  periods: string[];
};

export type Dashboard = {
  start: string;
  end: string;
  income: string;
  expenses: string;
  net: string;
  savings_rate: string | null;
  spending: { group: string; amount: string }[];
  budgets: BudgetLine[];
  review: number;
  recent: Tx[];
  notes: string[];
};
