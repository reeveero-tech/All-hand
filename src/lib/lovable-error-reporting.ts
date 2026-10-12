/**
 * Error reporting shim for the root TanStack UI.
 *
 * Originally imported by src/routes/__root.tsx from a module that was never
 * committed, which broke `npm run build` at the repo root (UNRESOLVED_IMPORT,
 * R-013). This is a minimal, dependency-free implementation: errors are
 * logged to the console. A real reporter (Sentry, etc.) can be wired in
 * later without touching call sites.
 */

export interface LovableErrorContext {
  boundary?: string;
  [key: string]: unknown;
}

export function reportLovableError(
  error: unknown,
  context: LovableErrorContext = {},
): void {
  // eslint-disable-next-line no-console
  console.error("[lovable-error-reporting]", error, context);
}

export default reportLovableError;
