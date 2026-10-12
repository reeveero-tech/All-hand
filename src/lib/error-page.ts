/**
 * Error page renderer shim for the root TanStack UI.
 *
 * Imported by src/server.ts but never committed (R-013). Minimal
 * dependency-free implementation.
 */

export function renderErrorPage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AGOS — Error</title>
</head>
<body>
  <h1>Something went wrong</h1>
  <pre>${message.replace(/</g, "<")}</pre>
</body>
</html>`;
}

export default renderErrorPage;
