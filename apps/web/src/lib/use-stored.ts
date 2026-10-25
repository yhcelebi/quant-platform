"use client";

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener("qp-storage", onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener("qp-storage", onChange);
  };
}

function read(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

/** A value kept in this browser's localStorage (``fallback`` when absent or blocked). */
export function useStored(key: string, fallback: string): string {
  return useSyncExternalStore(
    subscribe,
    () => read(key, fallback),
    () => fallback,
  );
}

export function store(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
    window.dispatchEvent(new Event("qp-storage"));
  } catch {
    // storage blocked: the value lasts for this page only
  }
}
