/**
 * Shared types, mirroring the backend contracts in app/api/schemas.py.
 *
 * Hand-written rather than generated from OpenAPI: the surface is small, and a
 * generator would add a build step and a whole class of drift-at-build-time
 * problems for very little gain at this size.
 */

export interface Citation {
  label: string;
  quote: string | null;
  guest: string | null;
  episode_title: string | null;
  episode_slug: string | null;
  /** Display timestamp e.g. "1:04:22". Null when the source has no timestamps. */
  timestamp: string | null;
  /** Deep link to the exact second of the source video. */
  source_url: string | null;
  score: number | null;
}

export interface Message {
  id: string;
  seq: number;
  role: "user" | "assistant" | "system";
  content: string;
  skill: string | null;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  insufficient_evidence: boolean;
  created_at: string;
  citations: Citation[];
}

export interface Session {
  id: string;
  user_id: string;
  title: string | null;
  provider: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ArtifactSummary {
  id: string;
  kind: "markdown" | "html";
  title: string;
  template: string | null;
  version: number;
  word_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface Artifact extends ArtifactSummary {
  content: string;
  session_id: string;
  message_id: string | null;
  metadata: Record<string, unknown>;
}

export interface ProviderInfo {
  name: string;
  model: string;
  healthy: boolean;
  reason: string;
  supports_native_tools: boolean;
  is_active: boolean;
  is_fallback: boolean;
  runtime?: string | null;
}

export interface ModelsStatus {
  configured_provider: string;
  fallback_provider: string;
  effective_provider: string;
  degraded: boolean;
  providers: ProviderInfo[];
  skills: { name: string; description: string }[];
  retrieval: Record<string, unknown>;
}

export interface CorpusStatus {
  episodes: number;
  chunks: number;
  source_commit: string | null;
  last_ingested_at: string | null;
  ready: boolean;
}

export interface ApiError {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
  /** What the operator should actually do. Rendered in error states. */
  remediation?: string;
}

/** Events streamed from POST /api/sessions/{id}/messages. */
export type StreamEvent =
  | { type: "provider"; provider: string; model: string; was_fallback: boolean }
  | { type: "skill"; skill: string; reason: string }
  | { type: "status"; stage: string; message: string }
  | { type: "evidence"; evidence: Citation[] }
  | { type: "token"; text: string }
  | { type: "correction"; text: string }
  | { type: "artifact"; artifact: GeneratedArtifact }
  | { type: "error"; error: ApiError }
  | {
      type: "done";
      message_id: string | null;
      artifact_id: string | null;
      skill: string;
      provider: string;
      model: string;
      was_fallback: boolean;
      insufficient_evidence: boolean;
      latency_ms: number;
      citations: Citation[];
      persisted: boolean;
      meta: Record<string, unknown>;
    };

export interface GeneratedArtifact {
  kind: "markdown" | "html";
  title: string;
  content: string;
  template: string | null;
  metadata: Record<string, unknown>;
}
