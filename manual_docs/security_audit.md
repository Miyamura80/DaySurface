# DaySurface Security Audit

_Full-codebase security review. Each finding below was traced through the actual
data flow and hand-verified against the source; items that looked suspicious but
proved sound are listed under "Verified sound" so they are not re-investigated._

Scope: `api_server/` (auth, routes, middleware, billing), `mcp_server/` (tool
factory, enhancers, app_tools, the React iframe apps), `services/`, `db/`,
`common/`, `utils/`, `src/`, `.github/workflows/`, and deployment config
(Dockerfile, railway/render/smithery).

Overall the codebase is unusually well-hardened: JWTs are verified with
signature + audience + issuer and `algorithms=["RS256"]` (no `alg:none`, no
`verify=False`); all DB access is parameterized SQLAlchemy (no SQL injection);
`user_id` is overridden with the authenticated principal on every authenticated
transport (no IDOR); webhooks verify signatures; CORS is a scoped allow-list.
The findings that follow are the real, substantiated issues.

---

## Severity summary

| # | Severity | Finding | Primary location |
|---|----------|---------|------------------|
| 1 | **Critical** | All server secrets dumpable via `config_show`/`config_get` over the HTTP API to any `services:execute` key | `api_server/routes/services.py`, `services/config_svc.py` |
| 2 | **High** | Server-side config tampering + subprocess (`config_set`/`doctor_fix`) exposed on the same HTTP surface | `api_server/routes/services.py`, `services/config_svc.py`, `services/doctor_svc.py` |
| 3 | **High** | `webhook_subscribe` is an LLM-callable, unconfirmed email-exfiltration primitive (prompt injection) | `services/webhooks_svc.py`, `mcp_server/server.py` |
| 4 | **High** (misconfig) | WorkOS test-mode auth bypass - impersonate any user - gated only on `DEV_ENV`, which defaults to `dev` | `api_server/auth/workos_auth.py` |
| 5 | **Medium-High** | SSRF in webhook delivery: subscribe-time validation is TOCTOU + fail-open, delivery does not pin the IP | `services/webhooks_svc.py`, `services/webhook_delivery_svc.py` |
| 6 | **Medium** | Inconsistent "is production" detection across three files; `DEV_ENV=production` ships the default `SESSION_SECRET_KEY` | `common/global_config.py`, `api_server/billing/stripe_config.py` |
| 7 | **Medium** | Privileged AI auto-fix workflow can be steered by untrusted PR content (prompt injection → write access + secrets) | `.github/workflows/cursor_fix_ci_failures.yml` |
| 8 | **Medium** | `diagnose=True` logging renders frame locals (tokens, decrypted plaintext, email bodies) past the scrubber | `src/utils/logging_config.py` |
| 9 | **Medium** | Third-party GitHub Actions pinned to mutable tags on the OIDC-bearing release path; `curl \| bash` installers | `.github/workflows/*` |
| 10 | **Medium** | Unauthenticated `/ask` drives paid LLM inference with only per-IP rate limiting | `api_server/routes/ask.py` |
| 11 | **Low** | Docker image runs as root (no `USER`) | `Dockerfile` |
| 12 | **Low** | Verbose internal-error strings returned to callers on payment routes | `api_server/routes/agentic_payments.py` |
| 13 | **Low** | OAuth success page interpolates email without HTML-escaping | `api_server/routes/google_oauth.py` |
| 14 | **Low** | Broad default `["*"]` scope grant for interactive identities (latent) | `api_server/auth/unified_auth.py`, `authkit_auth.py` |
| 15 | **Low** | MCP app bundles `postMessage(…, "*")` + source-based (not origin-based) inbound trust | `mcp_server/apps/*/dist/mcp-app.html` |
| 16 | **Low** | Email HTML links keep `target` without forced `rel="noopener"` | `mcp_server/apps/gmail_inbox/src/sanitize.ts` |

The root cause of #1 and #2 is the same: **the HTTP transport auto-registers
every `@service` with no exclusion list, while the MCP transport maintains a
denylist** (`_EXCLUDED_DEFAULT_MCP_SERVICES`). The admin/introspection tools that
MCP deliberately hides are fully reachable over HTTP.

---

## 1. Critical - Full secret disclosure via `config_show` / `config_get` (HTTP)

`api_server/routes/services.py` registers one `POST /api/v1/services/{name}`
route per registry entry with no filtering:

```python
def _register_service_routes() -> None:
    discover_services()
    for entry in get_registry():
        _make_route(entry)  # every service, no denylist
```

The only gate is the `services:execute` scope - held by any `standard`-template
API key (`api_server/auth/scopes.py`). `config_show` returns the entire config
dump, and every secret is a plain `str` field (not `SecretStr`), so
`model_dump()` emits them in cleartext:

```python
# services/config_svc.py
def config_show(input: ConfigShowInput) -> ConfigShowResult:
    return ConfigShowResult(config=global_config.to_dict())  # to_dict() == model_dump()


# common/global_config.py
GOOGLE_TOKEN_ENC_KEY: str | None = None  # master key for ALL refresh tokens
BACKEND_DB_URI: str | None = None
STRIPE_SECRET_KEY: str | None = None
WORKOS_API_KEY: str | None = None
SESSION_SECRET_KEY: str = "change-me-in-production"
```

`config_get(key="GOOGLE_TOKEN_ENC_KEY")` returns a single secret surgically.

**Exploit:** any tenant/leaked API key → `POST /api/v1/services/config_show` →
receives `GOOGLE_TOKEN_ENC_KEY` + `BACKEND_DB_URI`. With those the attacker reads
the `google_tokens` table and decrypts **every user's Gmail refresh token**
(all-tenant mailbox takeover), plus every webhook secret, the Stripe key, and the
session-signing key. The MCP denylist (`config_*`, `doctor*`) proves these were
understood to be non-tenant-facing - the HTTP registration just never got the
same guard.

**Note for maintainers:** `tests/test_api_server.py:126` currently *asserts* that
`/api/v1/services/config_show` is a registered route, so the exposure is locked
in by a test. Deciding the intended surface for these introspection services is a
design call, which is why this is reported rather than silently patched.

**Fix:** give `_register_service_routes` the same exclusion set the MCP side uses
(skip `config_get/config_set/config_show/doctor/doctor_fix`), **or** gate them
behind an `ADMIN_READ`/`ADMIN_WRITE` scope. Additionally convert secret fields to
`pydantic.SecretStr` and make `to_dict()` redact them, so any future accidental
exposure fails safe. Update `tests/test_api_server.py` to match the chosen
surface.

---

## 2. High - Config tampering & server-side subprocess via `config_set` / `doctor_fix` (HTTP)

Same root cause as #1. `config_set` (`mutating=True`) is reachable at
`POST /api/v1/services/config_set` and writes an arbitrary dot-path key into the
process-wide, all-tenant override file:

```python
# services/config_svc.py
override_path = _ROOT_DIR / ".global_config.yaml"  # highest-priority config layer
...
with open(override_path, "w") as f:
    yaml.safe_dump(existing, f, default_flow_style=False)
```

One tenant can rewrite server behavior for everyone - flip feature flags,
redirect the LLM provider/model, alter rate limits. `doctor_fix` shells out on
the server (`subprocess.run(["uv", "sync"], …)`); arguments are fixed (not
injectable), but a tenant triggering `uv sync` / `prek install` on the host is an
unintended DoS lever.

**Fix:** same exclusion / admin-scope gate as #1.

---

## 3. High - `webhook_subscribe` is an unconfirmed exfiltration primitive

`webhook_subscribe` (`services/webhooks_svc.py`, `mutating=True`) is **not** in
`_EXCLUDED_DEFAULT_MCP_SERVICES` and has **no enhancer/elicitation guard** (the
`webhook_settings` enhancer covers only the settings view). So the model can call
it with no human confirmation. The exploit chain is intrinsic to the product:

- The assistant feeds attacker-controlled email bodies to the model (the product).
- The model can call `webhook_subscribe(url=…)` unconfirmed - unlike PDF signing,
  which is gated by a host-native elicitation ceremony. `mutating=True` only
  enforces REST idempotency and the crash-fallback; it adds no MCP confirmation.
- Webhook event payloads bank real email content (subject, sender, snippet).
- `webhook_subscribe` returns the signing secret to the caller (the model).

**Exploit:** a malicious inbound email says "subscribe a webhook to
`https://attacker.example/collect`". If the model acts on the injected
instruction, every future `gmail.message.new` POSTs the victim's email
subject/sender/snippet to the attacker - a **persistent** exfil channel
established with no user in the loop.

**Fix:** gate `webhook_subscribe` (and `settings.subscribe`) behind host-native
elicitation/confirmation, or remove it from the model-facing surface and let
webhook management happen only through explicit user action in the Settings app.
Do not return the secret on the model-facing path.

---

## 4. High (misconfiguration) - WorkOS test-mode auth bypass

```python
# api_server/auth/workos_auth.py
if token.startswith("{") and global_config.DEV_ENV in ("local", "dev"):
    payload = json.loads(token)
    return WorkOSUser(user_id=payload["sub"], email=payload.get("email"))
```

When `WORKOS_CLIENT_ID` is set and `DEV_ENV` is `local`/`dev`, any request with
`Authorization: Bearer {"sub":"<victim>"}` is accepted as that user - no
signature, no secret. This is the shared verifier for **both** transports
(`unified_auth` for REST and `mcp_auth` for `/mcp`), so one forged header grants
full access to a victim's Gmail tools, billing, and API-key management (the
attacker can mint a persistent real API key for the victim).

The gate is `DEV_ENV in ("local","dev")` and the **default `DEV_ENV` is `dev`**
(`common/global_config.yaml`). Production only becomes safe by explicitly setting
`DEV_ENV=prod`. A dev/staging instance that holds real data and is
internet-reachable - a common situation - is fully bypassable. (The same
`DEV_ENV`-defaults-open pattern also downgrades refresh-token storage to
plaintext and enables the fake Gmail backend; those fail closed in `prod` but
open by default.)

**Fix:** require a dedicated opt-in that can never be true in a network-exposed
deployment (e.g. `ALLOW_TEST_TOKENS`, default false, **and** assert the client is
loopback), or delete the bypass and mint a real RS256 test key in the dev
harness.

---

## 5. Medium-High - SSRF in webhook delivery (TOCTOU + fail-open, no IP pinning)

Subscriber URLs are validated only at subscribe time, and delivery re-resolves
DNS with no pinning and no re-validation:

```python
# services/webhook_delivery_svc.py
with httpx.Client(timeout=_HTTP_TIMEOUT_S) as client:
    resp = client.post(url, content=body, headers=headers)  # re-resolves, unpinned


# services/webhooks_svc.py - validation fails OPEN on resolution failure
def _candidate_ips(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []  # empty -> guard loop never runs -> passes
```

Two bypasses: (a) register a name that doesn't resolve yet (passes), then point
it at `169.254.169.254` / `10.x`; (b) DNS-rebind after subscribe. An attacker
triggers delivery on demand by emailing the victim. Result: blind SSRF POST into
the internal network / cloud metadata.

The correct defense already exists in-repo: `services/image_proxy.py`
`_validate_and_pin` resolves, requires a global address, **pins the connection to
the validated IP** with Host/SNI preserved, sets `trust_env=False`, and
re-validates every redirect hop - and fails *closed* on empty resolution. It was
simply not applied to webhook delivery.

**Fix:** reuse the `image_proxy` approach in webhook delivery; make
`_candidate_ips` fail closed.

---

## 6. Medium - Inconsistent "is production" detection

Three different predicates decide "are we in prod":

- `common/global_config.py` rejects the default `SESSION_SECRET_KEY` only when
  `DEV_ENV == "prod"` (exact match).
- `api_server/billing/stripe_config.py` treats anything **not** in
  `{dev, development, staging, test, local}` as production.
- `api_server/auth/workos_auth.py` keys the bypass off `DEV_ENV in ("local","dev")`.

**Exploit:** deploy with the natural value `DEV_ENV=production`. The secret
validator does *not* fire (default `"change-me-in-production"` accepted), yet
`stripe_config.is_production()` returns true, so **live Stripe keys are used with
the default session secret**. `SESSION_SECRET_KEY` HMAC-signs the OAuth `state`
and the session cookie; with a known key an attacker forges a valid `state` for
an arbitrary `user_id` (Google-account-linking forgery against the callback) and
tampers session cookies.

**Fix:** one shared `is_production()` helper everywhere; make the secret check
fire for every environment classified as prod, and always reject the literal
default regardless of env.

---

## 7. Medium - Privileged AI auto-fix workflow on untrusted PR content

`.github/workflows/cursor_fix_ci_failures.yml` runs on `workflow_run` (privileged
base-repo context) with `contents: write` + `pull-requests: write` +
`CURSOR_API_KEY` + `GITHUB_TOKEN`, and prompts an agent to read the failing PR's
diff/comments (`gh pr diff`, `gh pr view`) - fully attacker-controlled for fork
PRs - then edit code and `git push --force`. This is a textbook prompt-injection →
privileged-agent path: injected text in a PR title/body/diff can steer the agent
to exfiltrate the secrets or push attacker code.

Currently **dormant**: the trigger is `workflows: [Test]` and no workflow named
"Test" exists - but it activates the moment one is added. (The `jules-*.yml`
workflows are also AI-agent automations worth the same scrutiny.)

**Fix:** never feed untrusted PR text to an agent holding write scopes/secrets;
gate on `github.event.workflow_run.head_repository.fork == false`; drop to
read-only permissions and post comments from a separate unprivileged job; remove
`--force`.

---

## 8. Medium - `diagnose=True` logging leaks frame locals past the scrubber

```python
# src/utils/logging_config.py
logger.add(
    sys.stderr, ..., backtrace=True, diagnose=True, catch=True, filter=log_filter
)
```

`diagnose=True` renders every stack frame's **local variable values** in
tracebacks. The scrubber (`scrub_sensitive_data`) only cleans `record["message"]`
and `exception.args`; it never touches the frame locals loguru reads from the
traceback. On any exception in a secret-handling path, locals like
`refresh_token`, `access_token`, the decrypted `plaintext`
(`common/token_encryption.py`, `services/gmail_svc.py`), and email bodies/PII are
written to stderr in cleartext.

**Fix:** set `diagnose=False` (and consider `backtrace=False`) outside
`DEV_ENV in {local,dev}`.

---

## 9. Medium - Supply-chain: mutable action tags + `curl | bash` installers

Third-party actions are pinned to mutable tags rather than commit SHAs:
`astral-sh/setup-uv@v7` and `oven-sh/setup-bun@v2`. This matters most in
`release.yml`, which carries `id-token: write` (PyPI Trusted Publishing) on `v*`
tags - a poisoned `setup-uv@v7` there could hijack the publish or steal the OIDC
token. The repo already SHA-pins `pypa/gh-action-pypi-publish` and
`softprops/action-gh-release` in the same file, so the pattern is understood but
not applied to the setup actions. Separately, several workflows install tools via
unpinned `curl … | bash` (cursor, mcp-publisher, uv), including on the
OIDC-bearing `mcp-registry-publish.yml`.

**Fix:** SHA-pin every third-party action (`@<sha> # v7`); prefer the pinned
`astral-sh/setup-uv` action over the curl-pipe; for cursor/mcp-publisher,
download + verify a pinned checksum before executing. Consider Dependabot for
actions.

---

## 10. Medium - Unauthenticated `/ask` drives paid LLM inference

`api_server/routes/ask.py` is public (no auth); the only control is per-IP rate
limiting. An attacker rotating source IPs can run up unbounded LLM spend - there
is no global budget/concurrency ceiling. It ships inert (404 when `ask.enabled`
is false), which bounds exposure, but when enabled the cost-DoS surface is real.

**Fix:** add a global spend cap / concurrency ceiling independent of per-IP
limits.

---

## 11-16. Low

- **11 - Docker runs as root** (`Dockerfile`, no `USER`): any RCE/breakout runs as
  UID 0. This app parses email + PDFs and holds OAuth tokens. Add a non-root user
  and `USER` before `CMD`. (`.dockerignore` correctly excludes `.env`/`.git`.)
- **12 - Verbose error leakage** (`api_server/routes/agentic_payments.py`): raw
  `HTTPException(500, detail=str(exc))` bypasses the global scrubber and forwards
  SDK/internal detail to callers. Return a generic message; log detail
  server-side.
- **13 - OAuth success page** (`api_server/routes/google_oauth.py`): `_success_page`
  interpolates `email` without escaping while `_error_page` correctly uses
  `html.escape`. Email comes from Google's id_token over TLS (low risk); escape
  it for consistency/defense-in-depth.
- **14 - Broad default `["*"]` scope** (`unified_auth.py`, `authkit_auth._resolve_scopes`):
  interactive first-party identities get full access, making `require_scopes` a
  no-op for them. Documented and intentional today, but latent: if JWT/AuthKit
  issuance is ever broadened to less-trusted users, every token silently becomes
  unrestricted. Move to role-based scope assignment before any such change.
- **15 - `postMessage(…, "*")` in app bundles** (committed `dist/mcp-app.html`):
  outbound JSON-RPC (which can carry email content / secrets) is not origin-pinned,
  and inbound trust rests on the source-window reference, not `event.origin`.
  Comes from the upstream `@modelcontextprotocol/ext-apps` SDK and is mediated by
  the host sandbox in practice - pin origin if/when the SDK exposes the option.
- **16 - Missing `rel="noopener"`** (`mcp_server/apps/gmail_inbox/src/sanitize.ts`):
  DOMPurify preserves `target` on untrusted email links without adding
  `rel="noopener noreferrer"` (reverse tabnabbing). Modern browsers imply
  `noopener` for `target="_blank"`, so largely mitigated. Add an
  `afterSanitizeAttributes` hook.

---

## Verified sound (checked, no action needed)

- **JWT verification** (WorkOS + AuthKit): JWKS signature, `algorithms=["RS256"]`,
  audience + issuer pinned, expiry enforced. No `alg:none`, no `verify=False`.
- **API keys**: 256-bit `secrets.token_hex(32)`, SHA-256 lookup (unsalted is fine
  for high-entropy random tokens), revocation + expiry enforced, raw keys never
  logged. Create-key path forces new scopes to be a subset of the caller's.
- **Tenant isolation / IDOR**: both the REST route and the MCP tool factory
  override any client-supplied `user_id` with the authenticated principal; app
  tools do the same via `guard_user_id`; idempotency keys are namespaced
  `(user_id, route, key)`. No cross-user path found.
- **SQL injection**: none - all DB access is parameterized SQLAlchemy ORM; the
  many `.execute()` calls in `services/gmail_*` are Google API client calls, not
  SQL; migrations use static `sa.text()`.
- **Command injection / deserialization**: all `subprocess.run` use fixed argv, no
  shell; YAML uses `safe_load` everywhere; no `pickle`/`eval`/`exec`/`os.system`.
- **Webhooks (inbound)**: Stripe verifies `construct_event` and only falls back to
  the test secret in non-prod; Gmail Pub/Sub verifies the Google-signed OIDC
  token and fails closed outside dev; the internal `/renew` endpoint uses
  `hmac.compare_digest`.
- **OAuth `state`**: HMAC-SHA256 with a random nonce + 10-min expiry, verified with
  `hmac.compare_digest` (strong *provided* the session secret is strong - see #6).
- **Enhancer crash-fallback**: never re-executes a mutating service - reuses the
  completed result or re-raises. No double-charge/double-send path.
- **`image_proxy` SSRF guard**: strong - global-address check, connection pinned
  to the validated IP with Host/SNI preserved, `trust_env=False`, manual redirect
  re-validation, content-type + size caps. Fails closed. (This is the pattern #5
  should reuse.)
- **Email-body XSS**: single DOMPurify pass, sanitized string handed to
  `dangerouslySetInnerHTML` byte-for-byte, SVG/MathML dropped, inline styles
  scrubbed, remote images default-deny. Subjects/senders render as auto-escaped
  React text.
- **Secrets in repo**: none committed - the only matches are a deliberately-fake
  key in `tests/test_logging_security.py` and `whsec_example_*` mock secrets in
  `dev_preview` fixtures. `.env` is gitignored and excluded from the image.
- **CORS**: scoped allow-list from config, not wildcard-with-credentials.
- **No `pull_request_target`; no self-hosted runners.**

---

## Recommended remediation order

1. **#1 / #2** - add the HTTP service-registration exclusion (or admin-scope gate)
   and `SecretStr`-wrap secrets. Highest impact, lowest effort, closes all-tenant
   secret disclosure.
2. **#3** - gate `webhook_subscribe` behind explicit user confirmation or remove
   it from the model surface.
3. **#4 / #6** - unify production detection; make the test-token bypass require a
   loopback + explicit opt-in.
4. **#5** - pin + re-validate webhook delivery (reuse `image_proxy`).
5. **#7 / #9** - harden the AI auto-fix workflow and SHA-pin release-path actions.
6. **#8 / #10 / #11** - disable `diagnose` in prod, cap `/ask` spend, drop Docker
   to non-root.
7. Low items as cleanup.
