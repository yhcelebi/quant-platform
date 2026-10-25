"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ArrowLeftRight,
  ChartPie,
  FileSpreadsheet,
  Inbox,
  LayoutDashboard,
  Plus,
  Scale,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/transactions", label: "Transactions", icon: ArrowLeftRight },
  { href: "/add", label: "Add", icon: Plus },
  { href: "/review", label: "Review", icon: Inbox },
  { href: "/budgets", label: "Budgets", icon: ChartPie },
  { href: "/statements", label: "Statements", icon: Scale },
  { href: "/import", label: "Import", icon: FileSpreadsheet },
  { href: "/settings", label: "Settings", icon: Settings },
];
const MOBILE = ["/", "/transactions", "/add", "/review", "/budgets"];

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path.startsWith(href);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-56 shrink-0 border-r bg-sidebar p-3 md:block">
        <div className="px-2 pt-1 pb-4 text-sm font-semibold tracking-tight">Household books</div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                active(path, href) && "bg-accent font-medium text-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 overflow-x-hidden px-4 pt-4 pb-24 md:px-8 md:pt-8 md:pb-10">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {NAV.filter((n) => MOBILE.includes(n.href)).map(({ href, label, icon: Icon }) =>
          href === "/add" ? (
            <Link key={href} href={href} aria-label="Add expense" className="flex justify-center py-2">
              <span className="-mt-5 flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg">
                <Plus className="size-6" />
              </span>
            </Link>
          ) : (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex flex-col items-center gap-0.5 py-2 text-[11px] text-muted-foreground",
                active(path, href) && "text-foreground",
              )}
            >
              <Icon className="size-5" />
              {label}
            </Link>
          ),
        )}
      </nav>
    </div>
  );
}

export function PageHeader({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
      <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{title}</h1>
      {children}
    </div>
  );
}
