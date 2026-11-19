/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin, for example https://chapterandverse.example. Empty: same origin. */
  readonly VITE_API_BASE?: string;
  /** Cloudflare Turnstile site key. Empty: no bot check widget. */
  readonly VITE_TURNSTILE_SITE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
