export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "cv-theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

let currentMode: ThemeMode = "system";
let mediaAttached = false;
const listeners = new Set<() => void>();

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}

function readStoredTheme(): ThemeMode {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeMode(raw) ? raw : "system";
  } catch {
    return "system";
  }
}

function writeStoredTheme(mode: ThemeMode): void {
  try {
    if (mode === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, mode);
    }
  } catch {
    // Ignore storage errors (private browsing, blocked storage, quota).
  }
}

function systemPrefersDark(): boolean {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DARK_QUERY).matches
      : false;
  } catch {
    return false;
  }
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === "system") {
    return systemPrefersDark() ? "dark" : "light";
  }
  return mode;
}

function applyToDocument(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode);
  if (typeof document !== "undefined") {
    const root = document.documentElement;
    root.classList.toggle("dark", resolved === "dark");
    if (mode === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", mode);
    }
  }
  return resolved;
}

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

function onMediaChange(): void {
  if (currentMode === "system") {
    applyToDocument("system");
    notify();
  }
}

function attachMediaListener(): void {
  if (mediaAttached || typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return;
  }
  try {
    const mq = window.matchMedia(DARK_QUERY);
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", onMediaChange);
      mediaAttached = true;
    }
  } catch {
    // Ignore environments with partial matchMedia stubs.
  }
}

/** Read stored preference, apply `.dark` on `<html>`, and subscribe to system changes. */
export function initTheme(): ThemeMode {
  currentMode = readStoredTheme();
  applyToDocument(currentMode);
  attachMediaListener();
  notify();
  return currentMode;
}

export function getTheme(): ThemeMode {
  return currentMode;
}

export function getResolvedTheme(): ResolvedTheme {
  return resolveTheme(currentMode);
}

export function setTheme(mode: ThemeMode): void {
  currentMode = mode;
  writeStoredTheme(mode);
  applyToDocument(mode);
  notify();
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

if (typeof document !== "undefined") {
  initTheme();
}
