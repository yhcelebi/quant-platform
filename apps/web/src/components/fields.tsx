"use client";

import { label } from "@/lib/format";
import { cn } from "@/lib/utils";

const FIELD =
  "h-10 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

/** Native select (best on phones), styled like the shadcn inputs. */
export function NativeSelect({
  value,
  onChange,
  children,
  className,
  ...props
}: Omit<React.ComponentProps<"select">, "onChange"> & { onChange: (v: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(FIELD, "appearance-auto", className)}
      {...props}
    >
      {children}
    </select>
  );
}

/** Categories grouped by their group (Food, Transport...), expenses first. */
export function CategoryOptions({ categories }: { categories: string[] }) {
  const groups = new Map<string, string[]>();
  for (const c of categories) {
    const [root, group] = c.split(":");
    const key = `${root === "Income" ? "Income · " : ""}${group}`;
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  const keys = [...groups.keys()].sort((a, b) =>
    a.startsWith("Income") === b.startsWith("Income") ? a.localeCompare(b) : a.startsWith("Income") ? 1 : -1,
  );
  return (
    <>
      {keys.map((k) => (
        <optgroup key={k} label={k}>
          {groups.get(k)!.map((c) => (
            <option key={c} value={c}>
              {label(c)}
            </option>
          ))}
        </optgroup>
      ))}
    </>
  );
}
