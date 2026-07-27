import { useCallback, useEffect, useState } from "react";
import { extractStructuredContent, errMsg } from "./extract";

export type SubView = {
  id: string;
  url: string;
  event_types: string[] | null;
  active: boolean;
  created_at?: string | null;
};

export type Snapshot = {
  gmail_connected: boolean;
  gmail_email: string | null;
  watching: boolean;
  watch_expiration: string | null;
  push_available: boolean;
  subscriptions: SubView[];
};

type SecretResult = { id: string; secret: string };

// Minimal structural view of the ext-apps App the panel actually uses. The
// appContract test pins this against the real SDK surface.
export type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
};

type Props = { mcpApp: McpAppLike };

export function Settings({ mcpApp }: Props) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<SecretResult | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const raw = await mcpApp.callServerTool({
        name: "settings.get",
        arguments: {},
      });
      const data = extractStructuredContent<Snapshot>(raw);
      if (data) setSnap(data);
    } catch (err) {
      setError(errMsg(err));
    }
  }, [mcpApp]);

  // Initial data arrives via the triggering tool result; fall back to a fetch.
  useEffect(() => {
    const handler = (result: unknown) => {
      const data = extractStructuredContent<Snapshot>(result);
      if (data) setSnap(data);
    };
    mcpApp.ontoolresult = handler;
    if (!snap) void refresh();
    return () => {
      if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = undefined;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpApp, refresh]);

  const addEndpoint = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "settings.subscribe",
        arguments: { url: trimmed, event_types: null },
      });
      const res = extractStructuredContent<SecretResult>(raw);
      if (res?.secret) {
        setSecret({ id: res.id, secret: res.secret });
        setCopied(false);
      }
      setUrl("");
      await refresh();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const rotate = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "settings.rotate_secret",
        arguments: { subscription_id: id },
      });
      const res = extractStructuredContent<SecretResult>(raw);
      if (res?.secret) {
        setSecret({ id: res.id, secret: res.secret });
        setCopied(false);
      }
      await refresh();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      await mcpApp.callServerTool({
        name: "settings.unsubscribe",
        arguments: { subscription_id: id },
      });
      await refresh();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const copySecret = async () => {
    if (!secret) return;
    try {
      await navigator.clipboard?.writeText(secret.secret);
      setCopied(true);
    } catch {
      /* clipboard unavailable; the secret is visible for manual copy */
    }
  };

  return (
    <div className="wrap">
      <style>{CSS}</style>
      <h1>Settings</h1>
      {error && (
        <div className="banner err" role="alert">
          <span>{error}</span>
          <button
            className="ghost"
            onClick={() => {
              setError(null);
              void refresh();
            }}
          >
            Retry
          </button>
        </div>
      )}

      <section className="card">
        <h2>Gmail</h2>
        {snap?.gmail_connected ? (
          <div className="row">
            <span className="email">{snap.gmail_email ?? "connected"}</span>
            <span className={`pill ${snap.watching ? "on" : "off"}`}>
              {snap.watching ? "Watching inbox" : "Not watching"}
            </span>
          </div>
        ) : (
          <p className="muted">Gmail is not connected.</p>
        )}
      </section>

      <section className="card">
        <h2>Email webhooks</h2>
        {!snap?.push_available ? (
          <p className="muted">
            Email webhooks aren&apos;t enabled on this server. Ask your
            administrator to configure Pub/Sub push.
          </p>
        ) : (
          <>
            {secret && (
              <div className="banner secret">
                <div className="secret-head">
                  <strong>Signing secret</strong>
                  <span className="muted">shown once - save it now</span>
                </div>
                <code className="secret-val">{secret.secret}</code>
                <div className="secret-actions">
                  <button onClick={copySecret}>{copied ? "Copied" : "Copy"}</button>
                  <button className="ghost" onClick={() => setSecret(null)}>
                    Dismiss
                  </button>
                </div>
              </div>
            )}

            <div className="add">
              <input
                type="url"
                placeholder="https://your-service.com/webhook"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void addEndpoint();
                }}
                aria-label="Webhook endpoint URL"
              />
              <button disabled={busy || !url.trim()} onClick={addEndpoint}>
                Add endpoint
              </button>
            </div>

            {snap.subscriptions.length === 0 ? (
              <p className="muted">
                No endpoints yet. Add one to receive <code>gmail.message.new</code>{" "}
                events.
              </p>
            ) : (
              <ul className="subs">
                {snap.subscriptions.map((s) => (
                  <li key={s.id} className={s.active ? "" : "inactive"}>
                    <div className="sub-main">
                      <span className="sub-url">{s.url}</span>
                      <span className="sub-meta">
                        {s.event_types && s.event_types.length
                          ? s.event_types.join(", ")
                          : "all events"}
                        {!s.active && " · inactive"}
                      </span>
                    </div>
                    <div className="sub-actions">
                      <button className="ghost" disabled={busy} onClick={() => rotate(s.id)}>
                        Rotate
                      </button>
                      <button className="ghost danger" disabled={busy} onClick={() => remove(s.id)}>
                        Remove
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );
}

const CSS = `
:root { color-scheme: light dark; }
.wrap {
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 640px; margin: 0 auto; padding: 16px;
  --bg: #fff; --fg: #1a1a1a; --muted: #6b7280; --line: #e5e7eb;
  --accent: #2563eb; --danger: #dc2626; --on: #16a34a;
  --card: #f9fafb; --secretbg: #fef9c3; --secretline: #eab308;
  color: var(--fg);
}
@media (prefers-color-scheme: dark) {
  .wrap {
    --bg: #0b0b0f; --fg: #e5e7eb; --muted: #9ca3af; --line: #27272a;
    --accent: #60a5fa; --danger: #f87171; --on: #4ade80;
    --card: #16161c; --secretbg: #3f3a11; --secretline: #a16207;
  }
}
.wrap h1 { font-size: 20px; margin: 0 0 12px; }
.wrap h2 { font-size: 14px; margin: 0 0 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.email { font-weight: 600; }
.muted { color: var(--muted); }
.pill { font-size: 12px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line); }
.pill.on { color: var(--on); border-color: var(--on); }
.pill.off { color: var(--muted); }
.banner { border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
.banner.err { background: color-mix(in srgb, var(--danger) 14%, transparent); color: var(--danger); display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.banner.secret { background: var(--secretbg); border: 1px solid var(--secretline); }
.secret-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.secret-val { display: block; word-break: break-all; font-size: 13px; padding: 6px 8px; background: rgba(0,0,0,.06); border-radius: 6px; }
.secret-actions { display: flex; gap: 8px; margin-top: 8px; }
.add { display: flex; gap: 8px; margin-bottom: 12px; }
.add input { flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--fg); }
button { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button.ghost { background: transparent; color: var(--fg); border-color: var(--line); }
button.ghost.danger { color: var(--danger); border-color: var(--danger); }
.subs { list-style: none; margin: 0; padding: 0; }
.subs li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); }
.subs li.inactive { opacity: .55; }
.sub-main { min-width: 0; }
.sub-url { display: block; font-weight: 600; word-break: break-all; }
.sub-meta { font-size: 12px; color: var(--muted); }
.sub-actions { display: flex; gap: 6px; flex-shrink: 0; }
`;
