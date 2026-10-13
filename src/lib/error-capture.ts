/**
 * Error capture shim for the root TanStack UI.
 *
 * Another module that src/server.ts imports but was never committed
 * (R-013). Minimal dependency-free implementation.
 */

const captured: unknown[] = [];

export function captureError(error: unknown): void {
  console.error("[error-capture]", error);
  captured.push(error);
}

export function consumeLastCapturedError(): unknown | undefined {
  return captured.pop();
}

export default { captureError, consumeLastCapturedError };
