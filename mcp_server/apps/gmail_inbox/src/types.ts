export type LabelChip = {
  name: string;
  bg_color: string;
  text_color: string;
};

export type CuratedThread = {
  thread_id: string;
  subject?: string;
  from?: string;
  snippet?: string;
  last_message_at?: string;
  importance_score: number;
  reasons: string[];
  labels?: LabelChip[];
  has_draft?: boolean;
  draft_id?: string;
};

export type Attachment = {
  filename?: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  content_id?: string;
  data?: string;
};

export type ThreadMessage = {
  message_id: string;
  from?: string;
  to?: string;
  cc?: string;
  date?: string;
  subject?: string;
  body_text?: string;
  body_html?: string;
  attachments: Attachment[];
};

export type DraftAttachment = {
  filename?: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
};

export type Draft = {
  draft_id: string;
  to?: string;
  cc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
};

export type Thread = {
  thread_id: string;
  messages: ThreadMessage[];
  draft?: Draft;
};

export type CurateResult = { threads: CuratedThread[] };

// Curation-ledger shapes (inbox_get_curation). The dashboard renders banked
// host-LLM verdicts from the ledger, not a fresh deterministic recompute.
export type Coverage = { curated: number; stale: number; uncurated: number };

export type CurationRecord = {
  thread_id: string;
  bucket?: string | null;
  importance?: number | null;
  summary?: string | null;
  suggested_action?: string;
  draft_id?: string | null;
  ledger_status?: string;
};

export type GetCurationResult = { records: CurationRecord[]; coverage?: Coverage };

export type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
  openLink: (params: { url: string }) => Promise<unknown>;
  sendMessage?: (params: {
    role: string;
    content: { type: string; text: string }[];
  }) => Promise<unknown>;
};

export type ComposerDraft = {
  draft_id: string;
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
};

export type ComposerSaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: Date }
  | { kind: "error"; message: string }
  | { kind: "sending" }
  | { kind: "sent"; message_id: string };

export type FileAttachment = {
  filename: string;
  mime_type: string;
  data_base64: string;
  size: number;
};

export type ExistingAttachment = {
  filename: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
};
