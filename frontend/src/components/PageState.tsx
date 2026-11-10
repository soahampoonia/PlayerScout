import type { ReactNode } from "react";
import { ApiError } from "../api";

export function Message({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="message">
      <h2>{title}</h2>
      {children && <div className="message-body">{children}</div>}
    </div>
  );
}

export function Loading() {
  return (
    <p className="loading" role="status">
      Loading…
    </p>
  );
}

/** What to show when a page's request failed. */
export function LoadError({ error, what, retry }: { error: unknown; what: string; retry: () => void }) {
  if (error instanceof ApiError && error.status === 404) {
    return <Message title={`That ${what} was not found`} />;
  }
  return (
    <Message title={`Could not load this ${what}`}>
      <p>The API did not answer. If the stack is still starting, give it a few seconds.</p>
      <button type="button" className="button" onClick={retry}>
        Try again
      </button>
    </Message>
  );
}
