import * as React from "react";

interface ToastState {
  id: number;
  message: string;
}

export interface ToastContextValue {
  showToast: (message: string) => void;
  dismissToast: () => void;
}

const DEFAULT_TOAST_MS = 1600;

const ToastContext = React.createContext<ToastContextValue>({
  showToast: () => {},
  dismissToast: () => {},
});

export function ToastProvider({
  children,
  durationMs = DEFAULT_TOAST_MS,
}: {
  children: React.ReactNode;
  durationMs?: number;
}) {
  const [toast, setToast] = React.useState<ToastState | null>(null);
  const nextId = React.useRef(1);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = React.useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const dismissToast = React.useCallback(() => {
    clearTimer();
    setToast(null);
  }, [clearTimer]);

  const showToast = React.useCallback(
    (message: string) => {
      clearTimer();
      const id = nextId.current++;
      setToast({ id, message });
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setToast((current) => (current?.id === id ? null : current));
      }, durationMs);
    },
    [clearTimer, durationMs],
  );

  React.useEffect(() => clearTimer, [clearTimer]);

  const value = React.useMemo<ToastContextValue>(
    () => ({ showToast, dismissToast }),
    [showToast, dismissToast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="pointer-events-none fixed bottom-24 left-1/2 z-50 -translate-x-1/2"
      >
        {toast !== null && (
          <div
            key={toast.id}
            className="toast-in rounded-full border border-border bg-surface px-3.5 py-1.5 text-caption text-foreground shadow-card"
          >
            {toast.message}
          </div>
        )}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  return React.useContext(ToastContext);
}
