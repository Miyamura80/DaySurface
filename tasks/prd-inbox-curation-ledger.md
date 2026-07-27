# PRD: Inbox Curation Ledger

## Introduction

Today, "triage my inbox" is served by a single deterministic tool
(`gmail_curate_inbox`) that ranks threads by a fixed importance score and
renders a dashboard. It is cheap but not intelligent, and every request
recomputes from scratch. The alternative - letting the host LLM read every
email and reason - is intelligent but re-burns tokens on every pass and does
not persist anything.

This feature reframes curation as **persistent, incrementally-maintained state**
that the **host LLM** builds and reuses: a per-user **Curation Ledger** in the
database. The host LLM is the only entity that produces judgments (bucket,
summary, suggested action, reasoning); it writes them back through an explicit
tool; and those judgments are **banked** so subsequent reads cost almost no
tokens. Freshness is tracked against Gmail's `historyId` so a "deep" pass only
ever re-reasons over the delta of new or changed threads, not the whole inbox.

"Quick" vs "deep" stop being two tools or two intents. They are the same tool
surface at different depths, and the depth is the host model's judgement:

- **Quick** - read the ledger (`inbox_get_curation`), serve banked verdicts,
  optionally top up the small stale/uncurated delta.
- **Deep** - search broadly and headlessly (`inbox_search`), read threads,
  reason, and record judgments (`inbox_save_curation`) that make the next quick
  read cheaper.

There is no intent-partitioning and no routing logic: the effort dial is
emergent from (a) how much unknown delta exists and (b) how much of it the host
chooses to process this turn.

## Goals

- Persist host-LLM curation judgments per user, per thread, in the database.
- Make repeat "what's important?" requests near-zero-token by serving banked
  verdicts instead of re-reading email bodies.
- Bound the cost of a deep pass to `O(new/changed mail since last pass)` using
  Gmail `historyId` freshness, not `O(inbox)`.
- Keep the host LLM as the sole curator (authority), while leaving a clean
  extension point for an optional cheap server-side DSPY prior later.
- Encrypt derived email content (summaries, reasoning) at rest.
- Purge all ledger data for a user when they disconnect Gmail.
- Preserve identical behaviour across CLI / MCP / HTTP transports (the ledger
  services are pure `@service` functions), and make the MCP dashboard render
  from the ledger so views are consistent across sessions and clients.

## User Stories

### US-001: Curation ledger table + ORM model
**Description:** As a developer, I need a `thread_curation` table so host-LLM
judgments persist per user, per thread.

**Acceptance Criteria:**
- [ ] New Alembic migration `db/migrations/versions/009_add_thread_curation.py`
      creating table `thread_curation`.
- [ ] Columns: `user_id` (String, indexed), `thread_id` (String), `bucket`
      (String - e.g. `needs_reply` / `waiting_on` / `fyi` / `noise`),
      `importance` (Float, nullable, 0..1), `summary_enc` (LargeBinary,
      encrypted), `reasoning_enc` (LargeBinary, encrypted, nullable),
      `suggested_action` (String - `archive`/`reply`/`mark_done`/`none`),
      `draft_id` (String, nullable), `confidence` (Float, nullable),
      `state` (String - `pending`/`curated`/`acted`/`dismissed`),
      `curated_history_id` (String - Gmail historyId at curation time),
      `curator_version` (String - model/prompt version tag),
      `curated_at` / `updated_at` (timezone-aware DateTime).
- [ ] Unique constraint on `(user_id, thread_id)`; index on
      `(user_id, state)` and `(user_id, bucket)`.
- [ ] ORM model `db/models/thread_curation.py` following the existing
      `db/models/*.py` pattern; re-exported from `db/models/__init__.py`.
- [ ] `alembic upgrade head` and `alembic downgrade -1` both run cleanly.
- [ ] `make ci` passes.

### US-002: Encryption-at-rest helper for curation content
**Description:** As a developer, I need summary/reasoning text encrypted at rest,
reusing the project's existing key-rotation convention.

**Acceptance Criteria:**
- [ ] Summary and reasoning are stored encrypted (`*_enc: bytes`) with a
      `key_id` column mirroring `db/models/google_tokens.py` (`refresh_token_enc`
      + `key_id`) so keys can rotate.
- [ ] A small encrypt/decrypt helper (or reuse of the existing Google-token
      crypto util) is used by the ledger services; plaintext is never persisted.
- [ ] Unit test round-trips encrypt -> store -> read -> decrypt.
- [ ] `make ci` passes.

### US-003: Pydantic contracts for curation
**Description:** As a developer, I need shared input/output schemas so all
transports agree on the curation shape.

**Acceptance Criteria:**
- [ ] `models/curation.py` defines `CurationRecord` (decrypted, host-facing),
      `CurationBucket` enum, `SuggestedAction` enum, and IO models:
      `GetCurationInput/Result`, `SaveCurationInput/Result`,
      `InboxSearchInput/Result`.
- [ ] `GetCurationResult` includes a `coverage` summary object:
      `{ curated: int, stale: int, uncurated: int }`.
- [ ] Models validate with example payloads in a unit test.
- [ ] `make ci` passes.

### US-004: `inbox_get_curation` service (cheap read path)
**Description:** As a host LLM, I want to read banked verdicts plus a coverage
summary so I can answer quickly and decide whether to go deeper.

**Acceptance Criteria:**
- [ ] `@service` `inbox_get_curation` in `services/inbox_curation_svc.py`
      returns curated rows for the user, filterable by `bucket`/`state` and by
      freshness (`fresh_only`).
- [ ] Freshness is computed by comparing each row's `curated_history_id` to the
      thread's current Gmail `historyId`; stale rows are flagged, not silently
      returned as fresh.
- [ ] Result includes `coverage` counts (curated / stale / uncurated) so the
      host can decide to top up.
- [ ] Performs **no** LLM inference and does **not** fetch full message bodies.
- [ ] Unit tests cover: fresh rows returned, stale detection, empty ledger
      (cold start) returns zero coverage.
- [ ] `make ci` passes.

### US-005: `inbox_search` service (headless deep primitive)
**Description:** As a host LLM, I want a headless broad search over recent mail
so I can go look at many threads when spending effort.

**Acceptance Criteria:**
- [ ] `@service` `inbox_search` returns thread/message summaries for a Gmail
      query + limit, headless (no UI side effects), reusing the existing
      `gmail_list_inbox` batch-fetch plumbing.
- [ ] Each returned item is annotated with its ledger status
      (`curated` / `stale` / `uncurated`) so the host can skip already-fresh
      threads and focus on the delta.
- [ ] Optionally accepts a `since_history_id` to return only changed threads via
      `users.history.list` (incremental delta); falls back to a normal query
      when no watermark is supplied.
- [ ] Unit tests cover annotation of mixed fresh/stale/uncurated results.
- [ ] `make ci` passes.

### US-006: `inbox_save_curation` service (explicit write-back, mutating)
**Description:** As a host LLM, I want to explicitly record my judgment for one
or many threads so my reasoning is banked and not repeated.

**Acceptance Criteria:**
- [ ] `@service(..., mutating=True)` `inbox_save_curation` accepts a batch of
      per-thread judgments and upserts ledger rows keyed by
      `(user_id, thread_id)`.
- [ ] On write, `curated_history_id` is set to the thread's current Gmail
      `historyId` and `curator_version` records the model/prompt version.
- [ ] Summary/reasoning are encrypted before persistence (US-002).
- [ ] Being `mutating=True`, the auto-generated REST route enforces
      `Idempotency-Key` (per template behaviour); CLI/MCP unaffected.
- [ ] Unit tests cover: insert new, update existing (re-curate advances
      `curated_history_id`), batch of mixed insert/update.
- [ ] `make ci` passes.

### US-007: Action services update the ledger
**Description:** As a host LLM, when I act on a thread the ledger should reflect
it so the state stays truthful.

**Acceptance Criteria:**
- [ ] `gmail_archive_thread`, `gmail_mark_thread_done`, and reply/draft flows
      update the corresponding ledger row's `state`
      (`acted` / `dismissed`) and `draft_id` where relevant.
- [ ] A thread archived/marked-done directly in Gmail (outside the agent) is
      reconciled on the next read via `historyId` staleness (row is not trusted
      as current).
- [ ] Unit tests cover state transition on archive and on draft-created.
- [ ] `make ci` passes.

### US-008: Deterministic score demoted to a prior
**Description:** As a host LLM, I want uncurated threads to still have a
provisional rank so a cold-start quick view isn't blank.

**Acceptance Criteria:**
- [ ] The existing `gmail_curate_svc.py` scoring is reused to provide a
      provisional `importance` for **uncurated** threads only, clearly marked as
      a heuristic prior (not an LLM judgment).
- [ ] For curated threads, the LLM-derived bucket/importance takes precedence
      over the heuristic.
- [ ] Unit test asserts curated rows override the prior; uncurated rows expose
      the prior with a `provisional=true` marker.
- [ ] `make ci` passes.

### US-009: Dashboard renders from the ledger
**Description:** As a user, I want the inbox dashboard to show the persisted
curation so it's consistent across sessions and clients.

**Acceptance Criteria:**
- [ ] The MCP inbox app (`mcp_server/apps/gmail_inbox`) and its enhancer render
      from `inbox_get_curation` output (banked verdicts + coverage) rather than
      a fresh deterministic compute.
- [ ] Coverage (e.g. "3 stale, 9 not yet triaged") is visible in the UI.
- [ ] Existing `tests/test_mcp_e2e.py`-style wire assertions updated/added for
      the new payload shape.
- [ ] Verify the rendered dashboard in a browser using the dev-browser / run
      skill.
- [ ] `make ci` passes.

### US-010: Tool descriptions rewritten for the effort-neutral surface
**Description:** As a host LLM on claude.ai / ChatGPT web (where tool
descriptions are the only reliable channel), I want honest descriptions that let
me choose depth without a curate-vs-triage fork.

**Acceptance Criteria:**
- [ ] `inbox_get_curation`, `inbox_search`, `inbox_save_curation` descriptions
      state the read-cheap / search-deep / record-judgment roles and reference
      the `coverage` counts as the signal for whether to go deeper.
- [ ] `gmail_curate_inbox` description no longer claims sole ownership of
      "triage"; it points to the ledger surface. (Kept for backward compat /
      quick dashboard; not deleted.)
- [ ] Action-tool descriptions note they update the ledger and, during a triage
      pass, to continue to the next uncurated/stale thread.
- [ ] No description asserts a hard loop; the coverage counts and per-item ledger
      status carry the "continue" signal instead.
- [ ] `make ci` passes.

### US-011: Purge ledger on Gmail disconnect
**Description:** As a user, when I disconnect Gmail I want all my curation data
deleted.

**Acceptance Criteria:**
- [ ] `gmail_disconnect` deletes all `thread_curation` rows for the user in the
      same operation that revokes tokens.
- [ ] Unit test asserts rows are gone after disconnect.
- [ ] `make ci` passes.

## Functional Requirements

- FR-1: The system must persist per-user, per-thread curation judgments in a
  `thread_curation` table, unique on `(user_id, thread_id)`.
- FR-2: Curation judgments (bucket, importance, suggested action, summary,
  reasoning, confidence, state) must be produced only by the host LLM and
  written only through the explicit `inbox_save_curation` tool.
- FR-3: `summary` and `reasoning` must be encrypted at rest with a `key_id`
  supporting rotation, matching the `google_tokens` convention.
- FR-4: `inbox_get_curation` must return banked verdicts plus a `coverage`
  summary (`curated` / `stale` / `uncurated`) and must not perform LLM inference
  or fetch full bodies.
- FR-5: Freshness must be determined by comparing a row's `curated_history_id`
  to the thread's current Gmail `historyId`; changed threads must be reported as
  stale.
- FR-6: `inbox_search` must be a headless primitive that annotates each result
  with its ledger status and can operate incrementally via `since_history_id`.
- FR-7: `inbox_save_curation` must be `mutating=True` (REST idempotency enforced)
  and must upsert, advancing `curated_history_id` and `curator_version` on
  write.
- FR-8: Action tools (archive, mark-done, reply/draft) must update the relevant
  ledger row's `state`/`draft_id`.
- FR-9: The deterministic score must be reused only as a provisional prior for
  uncurated threads, subordinate to LLM judgments.
- FR-10: The MCP dashboard must render from `inbox_get_curation`.
- FR-11: `gmail_disconnect` must purge all of a user's `thread_curation` rows.
- FR-12: All new curation logic must live in pure `@service` functions so CLI,
  MCP, and HTTP behave identically.

## Non-Goals (Out of Scope)

- **No server-side autonomous curator in v1.** The host LLM is the only curator.
  A cheap server-side DSPY prior is explicitly deferred; the design must leave a
  clean seam for it (e.g. a `curator_version` tag and a pluggable write path)
  but not implement it.
- No background/scheduled pre-curation job in v1 (durable state makes it
  possible later; not built now).
- No new importance-scoring algorithm - the existing deterministic score is
  reused only as a prior.
- No changes to Gmail sending semantics; replies remain draft-first.
- No cross-user or shared curation; the ledger is strictly per user.
- No new MCP primitive (no reliance on Resources/Prompts/skills-over-MCP) - the
  web-client channel remains tool descriptions.

## Design Considerations

- The dashboard becomes a **view over persistent state**, so it must clearly
  distinguish LLM-curated threads from provisional (heuristic-prior) ones, and
  surface coverage/staleness so the user understands what has and hasn't been
  triaged.
- Reuse the existing inbox app shell; add coverage/staleness affordances rather
  than a new UI.

## Technical Considerations

- **Freshness mechanism (chosen):** Gmail `historyId`. Store a per-row
  `curated_history_id`; a thread is fresh iff its current history id has not
  advanced past the stored value. This also self-corrects for out-of-band user
  actions (manual archive in Gmail). A simpler `last_message_at` comparison is a
  fallback if `historyId` plumbing proves heavy for v1 - see Open Questions.
- **Encryption:** reuse the `google_tokens` `refresh_token_enc` + `key_id`
  pattern (`db/models/google_tokens.py`) for `summary_enc` / `reasoning_enc`.
- **Migration:** next version is `009` (latest is
  `008_add_idempotency_keys.py`).
- **Idempotency:** `inbox_save_curation` is `mutating=True`, so it inherits the
  template's REST `Idempotency-Key` enforcement
  (`api_server/idempotency.py:execute_idempotent`) automatically.
- **Transport parity:** ledger logic stays in `services/`; MCP auto-registers on
  import; the dashboard enhancer stays MCP-only and never changes CLI/API
  behaviour.
- **Token economics:** the win is that a deep pass serializes reasoning into
  compact ledger rows; later reads cost ~tens of tokens per thread (row) instead
  of hundreds–thousands (full body). Deep-pass cost scales with the
  `historyId` delta, not inbox size.

## Success Metrics

- A repeat "what's important right now?" after a deep pass fetches **zero** full
  message bodies and runs **zero** LLM inference in the service layer (served
  from the ledger).
- A deep pass over an inbox with N new/changed threads reads only those N
  threads (verified by counting `get_thread`/`history.list` calls), independent
  of total inbox size.
- Disconnecting Gmail leaves zero `thread_curation` rows for the user.
- No regression in CLI/MCP/HTTP parity for existing Gmail tools (`make ci` and
  MCP e2e green).

## Open Questions

- **Freshness granularity for v1:** commit to full `historyId` incremental sync
  (via `users.history.list` + per-user watermark), or ship `last_message_at`
  comparison first and layer `historyId` in later? (Leaning `historyId` for the
  self-correcting property, accepting slightly more plumbing.)
- **Coverage staleness cost:** computing `stale` counts may require a light
  Gmail call per read to learn current history ids - is a per-user cached
  history watermark (refreshed on a bounded interval) acceptable to keep the
  quick path truly cheap?
- **Retention TTL:** beyond purge-on-disconnect, should curated rows expire
  after some inactivity window?
- **Curator versioning policy:** when the host prompt/model changes, do we
  bulk-invalidate by bumping `curator_version`, or let staleness handle it
  organically?
- **DSPY prior seam:** confirm the intended interface for the deferred
  server-side prior so `curator_version` and the write path are shaped to accept
  it without a schema change.
