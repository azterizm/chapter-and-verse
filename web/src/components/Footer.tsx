import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { useSyncExternalStore } from "react";
import { getTheme, setTheme, subscribeTheme, type ThemeMode } from "../lib/theme";
import { cn } from "../lib/utils";
import {
  FOOTER_NO_PERSONAL_DATA,
  FOOTER_NOT_ADVICE,
  FOOTER_OGL,
  FOOTER_SNAPSHOT_UNKNOWN,
  footerSnapshot,
  formatDate,
  THEME_DARK,
  THEME_LABEL,
  THEME_LIGHT,
  THEME_SYSTEM,
} from "../text";

export const SOURCE_CODE_URL = "https://github.com/azterizm/chapter-and-verse";
const OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/";

const THEME_OPTIONS: ReadonlyArray<{ mode: ThemeMode; label: string; Icon: LucideIcon }> = [
  { mode: "system", label: THEME_SYSTEM, Icon: Monitor },
  { mode: "light", label: THEME_LIGHT, Icon: Sun },
  { mode: "dark", label: THEME_DARK, Icon: Moon },
];

export function Footer({ snapshot, wide = false }: { snapshot: string | null; wide?: boolean }) {
  const mode = useSyncExternalStore(subscribeTheme, getTheme, () => "system");
  const snapshotText = footerSnapshot(snapshot ? formatDate(snapshot) : FOOTER_SNAPSHOT_UNKNOWN);

  return (
    <footer className="border-t border-border bg-background text-caption text-muted-foreground">
      <div
        className={cn(
          "mx-auto flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-4 sm:px-6",
          wide ? "max-w-[728px] lg:max-w-[1120px]" : "max-w-[728px]",
        )}
      >
        <p className="min-w-[200px] flex-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>{FOOTER_NOT_ADVICE}</span>
          <span aria-hidden="true">·</span>
          <span>{FOOTER_NO_PERSONAL_DATA}</span>
          <span aria-hidden="true">·</span>
          <a
            href={OGL_URL}
            rel="noopener noreferrer"
            target="_blank"
            className="underline decoration-border underline-offset-2 transition-colors hover:text-foreground"
          >
            {FOOTER_OGL}
          </a>
          <span aria-hidden="true">·</span>
          <span>{snapshotText}</span>
        </p>

        <div
          role="group"
          aria-label={THEME_LABEL}
          className="inline-flex h-8 shrink-0 items-center rounded-full border border-border bg-surface p-0.5"
        >
          {THEME_OPTIONS.map(({ mode: optMode, label, Icon }) => {
            const active = mode === optMode;
            return (
              <button
                key={optMode}
                type="button"
                aria-label={label}
                aria-pressed={active}
                title={label}
                onClick={() => setTheme(optMode)}
                className={cn(
                  "inline-flex h-full w-7 cursor-pointer items-center justify-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  active ? "bg-primary-soft text-primary" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            );
          })}
        </div>
      </div>
    </footer>
  );
}
