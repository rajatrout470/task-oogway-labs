/**
 * API client.
 *
 * Two notable choices:
 *
 * 1. **SSE via fetch + ReadableStream, not EventSource.** EventSource cannot
 *    issue POST requests or send a JSON body, and our chat turn needs both. So
 *    we read the response body as a stream and parse SSE frames ourselves.
 *
 * 2. **Errors are normalised into ApiError.** The backend has one error shape
 *    with a `remediation` field; preserving it end-to-end is what lets the UI
 *    tell a user "run `ollama pull ...`" instead of "something went wrong".
 */

import type {
  Artifact,
  ArtifactSummary,
  ApiError,
  CorpusStatus,
  Message,
  ModelsStatus,
  Session,
  StreamEvent,
} from "./types";

const BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

/** Error carrying the backend's structured payload, including remediation. */
export class ApiRequestError extends Error {
  constructor(
    public readonly error: ApiError,
    public readonly status: number,
  ) {
    super(error.message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    // A network-level failure means the backend is unreachable — a different
    // and more actionable condition than an HTTP error status.
    throw new ApiRequestError(
      {
        code: "network_error",
        message: "Could not reach the backend.",
        remediation: `Is the API running at ${BASE_URL}? Try: docker compose up`,
      },
      0,
    );
  }

  if (!response.ok) {
    let payload: ApiError = {
      code: "unknown_error",
      message: `Request failed with status ${response.status}`,
    };
    try {
      const body = await response.json();
      if (body?.error) payload = body.error;
    } catch {
      // Non-JSON error body (a proxy error page, say). Keep the default.
    }
    throw new ApiRequestError(payload, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Anonymous identity (PRD A3)
// ---------------------------------------------------------------------------

const USER_ID_KEY = "lenny.user_id";

/**
 * Return this browser's anonymous user id, creating one on first visit.
 *
 * Wrapped in try/catch because localStorage throws outright in some privacy
 * modes — a user with storage disabled should still get a working (if
 * non-persistent) session rather than a blank screen.
 */
export function getUserId(): string {
  try {
    const existing = localStorage.getItem(USER_ID_KEY);
    if (existing) return existing;
    const created = crypto.randomUUID();
    localStorage.setItem(USER_ID_KEY, created);
    return created;
  } catch {
    return crypto.randomUUID();
  }
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  getModels: () => request<ModelsStatus>("/api/models"),

  getCorpus: () => request<CorpusStatus>("/api/corpus"),

  createSession: (userId: string) =>
    request<Session>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        user_metadata: {
          user_agent: navigator.userAgent.slice(0, 200),
          locale: navigator.language,
        },
      }),
    }),

  listSessions: (userId: string) =>
    request<Session[]>(`/api/sessions?user_id=${encodeURIComponent(userId)}`),

  getSession: (sessionId: string) =>
    request<{
      session: Session;
      messages: Message[];
      artifacts: ArtifactSummary[];
    }>(`/api/sessions/${sessionId}`),

  deleteSession: (sessionId: string) =>
    request<void>(`/api/sessions/${sessionId}`, { method: "DELETE" }),

  getArtifact: (artifactId: string) => request<Artifact>(`/api/artifacts/${artifactId}`),

  updateArtifact: (artifactId: string, content: string) =>
    request<Artifact>(`/api/artifacts/${artifactId}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),

  /** URL for the sandboxed iframe. Served with a restrictive CSP. */
  artifactRenderUrl: (artifactId: string) =>
    `${BASE_URL}/api/artifacts/${artifactId}/render`,

  artifactDownloadUrl: (artifactId: string) =>
    `${BASE_URL}/api/artifacts/${artifactId}/download`,
};

// ---------------------------------------------------------------------------
// Streaming chat
// ---------------------------------------------------------------------------

export interface SendMessageOptions {
  sessionId: string;
  message: string;
  skill?: string | null;
  onEvent: (event: StreamEvent) => void;
  signal?: AbortSignal;
}

/**
 * Send a message and dispatch each streamed event.
 *
 * Buffering note: a network chunk can split an SSE frame anywhere, including
 * mid-JSON. We accumulate into a buffer and only parse complete frames
 * (terminated by a blank line), so a token boundary never corrupts an event.
 */
export async function sendMessage({
  sessionId,
  message,
  skill,
  onEvent,
  signal,
}: SendMessageOptions): Promise<void> {
  let response: Response;

  try {
    response = await fetch(`${BASE_URL}/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, skill: skill ?? null }),
      signal,
    });
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") return;
    onEvent({
      type: "error",
      error: {
        code: "network_error",
        message: "Could not reach the backend.",
        remediation: `Is the API running at ${BASE_URL}?`,
      },
    });
    return;
  }

  // Errors raised before the stream opens arrive as normal JSON.
  if (!response.ok) {
    let payload: ApiError = {
      code: "unknown_error",
      message: `Request failed with status ${response.status}`,
    };
    try {
      const body = await response.json();
      if (body?.error) payload = body.error;
    } catch {
      /* keep default */
    }
    onEvent({ type: "error", error: payload });
    return;
  }

  if (!response.body) {
    onEvent({
      type: "error",
      error: { code: "stream_error", message: "The response contained no stream." },
    });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            onEvent(JSON.parse(raw) as StreamEvent);
          } catch {
            // A malformed frame costs one event, never the whole stream.
            console.warn("Skipped unparseable SSE frame", raw.slice(0, 120));
          }
        }
      }
    }
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") return;
    onEvent({
      type: "error",
      error: {
        code: "stream_error",
        message: "The connection dropped while the assistant was responding.",
        remediation: "Check the backend logs and try again.",
      },
    });
  } finally {
    reader.releaseLock();
  }
}
