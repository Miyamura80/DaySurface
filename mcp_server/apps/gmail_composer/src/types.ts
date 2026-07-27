export type DraftAttachment = {
  attachment_id: string;
  filename: string;
  mime_type?: string;
  size?: number;
};

export type Draft = {
  draft_id: string;
  from?: string;
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
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
};

export type Thread = {
  thread_id: string;
  messages: ThreadMessage[];
};

export type McpAppLike = {
  ontoolresult?: (result: any) => void;  // eslint-disable-line @typescript-eslint/no-explicit-any
  callServerTool: (args: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>;
  updateModelContext: (args: {
    content: Array<{ type: "text"; text: string }>;
  }) => Promise<unknown>;
};

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: Date }
  | { kind: "error"; message: string };

export type SentState = { message_id: string };
