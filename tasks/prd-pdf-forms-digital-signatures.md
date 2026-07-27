# PRD: PDF Form Filling & User-Gated Digital Signatures

## 1. Introduction / Overview

Users receive fillable documents (NDAs, vendor forms, tax forms) as PDF email
attachments. Today the gmail_inbox MCP App can *preview* those PDFs (pdf.js),
but nothing can edit them. This feature lets the host LLM do the drudgery -
open a PDF attachment, fill its form fields (or overlay text on flat PDFs),
and attach the result to a reply draft - while **signing remains a
human-only ceremony**: the user explicitly types their name in a dedicated
MCP App (or, on hosts without iframe support, in a host-native elicitation
dialog). The model can never produce a signature.

Canonical scenario: *"Acme sent me an NDA - help me fill it out and sign
it."* The LLM fills 8 of 9 fields from context, asks the user for the rest in
chat, then requests a signature. The signing app renders the filled PDF; the
user reviews it, types their full name, ticks a consent checkbox, and
confirms via a host-native dialog. The server stamps the signature, embeds an
audit trail, cryptographically seals the document, and attaches it to a reply
draft - without the PDF bytes ever passing through the model's context.

## 2. Goals

- LLM can autonomously inspect and fill any AcroForm PDF attachment, and
  place text overlays on flat (field-less) PDFs, via **4 LLM-visible tools**.
- Signing is impossible without explicit human input: typed full name +
  consent checkbox in the signing app, plus a host-native elicitation
  confirmation. No LLM-visible tool performs a signature.
- Signed PDFs are tamper-evident: visible typed-name stamp + embedded audit
  trail (name, UTC timestamp, document SHA-256, consent record) + PAdES
  cryptographic seal from a server-held certificate (pyHanko).
- PDF bytes never enter the model's context: all state lives server-side in
  a document session keyed by `doc_id`; export to a Gmail draft happens
  server-to-server.
- The PDF domain is **isolatable as a future standalone add-on**: Gmail
  coupling confined to one bridge module, enforced by an import-linter
  contract (see §7).

## 3. Architecture Overview

```
                        LLM-visible (headless, autonomous OK)
                 ┌────────────────────────────────────────────────┐
 Gmail attachment│  pdf_open(source)                              │
 ───────────────►│    → doc_id + AcroForm inventory               │
   (via bridge)  │      + text layout w/ coords (flat PDFs)       │
                 │  pdf_edit(doc_id, ops[], render_pages?)        │
                 │    ops: set_field | add_text   (mutating=True) │
                 │    → field states (+ page images via enhancer) │
                 │  pdf_request_signature(doc_id, placement)      │──── enhancer:
                 │    → status: "awaiting_user_signature"         │     send_app()
                 │  pdf_export(doc_id, destination)               │        │
                 │    → attach to Gmail draft server-side         │        ▼
                 └────────────────────────────────────────────────┘  ┌────────────┐
                 ┌────────────────────────────────────────────────┐  │ pdf_signer │
   App-only      │  pdf_signer.get_document(doc_id)   (bytes)     │◄─┤ MCP App    │
   (iframe only, │  pdf_signer.sign(doc_id, typed_name, consent)  │◄─┤ (pdf.js    │
   NEVER listed  │    1. assert status == awaiting_signature      │  │  preview + │
   to the LLM)   │    2. elicit() confirmation via host UI ◄─user │  │  type-name │
                 │    3. stamp appearance + audit metadata        │  │  ceremony) │
                 │    4. pyHanko PAdES seal w/ server cert        │  └────────────┘
                 │    5. status = signed (immutable)              │
                 └────────────────────────────────────────────────┘
```

Document state machine (server-enforced, stored in `pdf_documents` table):

```
 open ──pdf_edit──► open ──pdf_request_signature──► awaiting_signature
                                                        │        │
                                              user signs│        │user cancels
                                                        ▼        ▼
                                                     signed ──► open
                                                   (immutable:
                                                    edits and re-sign
                                                    requests rejected)
```

### Isolation seam (future add-on extraction)

The PDF domain must not know about Gmail except through one bridge module:

```
 models/pdf_forms.py           ─┐
 services/pdf_forms_svc.py      │  PDF core: bytes in → bytes out.
 services/pdf_signing.py        │  NO imports of services.gmail_* or
 mcp_server/apps/pdf_signer/    │  models.gmail. Sources/destinations are
 mcp_server/app_tools/pdf_signer.py │ abstract (`PdfSource`, `PdfDestination`).
 mcp_server/enhancers/pdf_*.py ─┘
 db: pdf_documents table (own migration, no FKs to Gmail data)

 services/pdf_gmail_bridge.py  ── the ONLY module that imports both sides:
                                  resolves source={message_id, attachment_id}
                                  to bytes, and destination={draft_id} to a
                                  gmail_add_attachment-equivalent call.
```

Enforced by a new `.importlinter` forbidden contract: PDF core modules may
not import `services.gmail_*`, `services.pdf_gmail_bridge`, or
`models.gmail`. Future extraction = move the core modules into their own
package and swap the bridge.

## 4. User Stories

### US-001: Document session storage
**Description:** As a developer, I need server-side PDF document sessions so
bytes never travel through the model's context and state survives restarts.

**Acceptance Criteria:**
- [ ] `pdf_documents` table (Alembic migration): `doc_id` (human-readable id,
      per repo convention), `user_id`, `status`
      (`open|awaiting_signature|signed`), `current_bytes`, `original_bytes`,
      `source_ref` (opaque JSON), `audit` (JSON), timestamps. No foreign keys
      into Gmail-related data.
- [ ] Repository helpers: create / load / update-bytes / transition-status;
      status transitions validated against the state machine (invalid
      transition raises).
- [ ] Size cap on stored bytes (config: `pdf_forms.max_document_bytes`,
      default 20 MB) with a clear error.
- [ ] Unit tests via `TestTemplate`; `make ci` passes.

### US-002: `pdf_open` - inspect a PDF into a session
**Description:** As the host LLM, I want one call that gives me everything
needed to plan edits: the field inventory, or (for flat PDFs) the text layout
with coordinates.

**Acceptance Criteria:**
- [ ] `models/pdf_forms.py`: `PdfOpenInput` with `source` union - v1 variant
      `gmail_attachment {message_id, attachment_id}`; union is extensible
      (discriminated on `type`) so non-Gmail variants slot in later.
- [ ] `@service pdf_open` returns `doc_id`, `page_count`, `has_acroform`,
      `fields[]` (name, type, current value, page, rect) via pypdf, and for
      flat PDFs `text_layout[]` (page, x, y, text line) so the LLM can anchor
      overlays; layout capped (config) to keep responses bounded.
- [ ] Source resolution goes through `services/pdf_gmail_bridge.py`; the core
      service accepts bytes and never imports Gmail modules.
- [ ] Non-PDF attachment → clear error naming the detected mime type.
- [ ] Tests: AcroForm fixture PDF and flat fixture PDF; `make ci` passes.

### US-003: `pdf_edit` - batch fill and overlay
**Description:** As the host LLM, I want to apply all my edits in one call
and optionally get page images back to verify placement.

**Acceptance Criteria:**
- [ ] `ops` is a discriminated-union list: `{op:"set_field", name, value}`
      (checkbox/radio/choice values validated against field options) and
      `{op:"add_text", page, x, y, text, font_size?}` (pypdf FreeText
      annotation or content-stream stamp).
- [ ] Registered with `mutating=True` (API gets Idempotency-Key enforcement).
- [ ] All ops applied atomically: any invalid op (unknown field, page out of
      range) fails the whole batch with a per-op error report; bytes
      unchanged.
- [ ] Rejected with a clear error when status is `awaiting_signature` or
      `signed`.
- [ ] Returns updated field states; `render_pages` handled in US-004.
- [ ] Tests: fill AcroForm fixture, overlay flat fixture, mixed batch,
      rejection cases; `make ci` passes.

### US-004: Page rendering for model verification
**Description:** As the host LLM, I want rendered page images from
`pdf_open`/`pdf_edit` so I (vision-capable) can verify overlay placement.

**Acceptance Criteria:**
- [ ] `render_pages: list[int]` param on both tools; server rasterizes via
      `pypdfium2` at a config-capped DPI, page count per call capped
      (config, default 4).
- [ ] Enhancers (`mcp_server/enhancers/pdf_forms.py`) attach the PNGs as
      `ImageContent` blocks (same pattern as `gmail_attachment.py`); headless
      transports (CLI/API) return them base64 in the structured result.
- [ ] Tests assert `ImageContent` present on the wire in the MCP e2e suite
      (extend `tests/test_mcp_e2e.py`); `make ci` passes.

### US-005: `pdf_request_signature` - the ceremony gate
**Description:** As the host LLM, I want to hand off to the human for
signing once filling is done.

**Acceptance Criteria:**
- [ ] `@service pdf_request_signature(doc_id, placement)` where `placement`
      is a signature field name OR `{page, x, y}` for flat PDFs; validates
      doc is `open`, transitions to `awaiting_signature`, records placement.
- [ ] Enhancer attaches the `pdf_signer` app (`send_app("ui://mymcp/pdf_signer")`)
      when apps are available.
- [ ] Structured result contains only `{status: "awaiting_user_signature"}`
      guidance text telling the model the user must complete signing - never
      any signature-producing affordance.
- [ ] Calling it on an already-`signed` doc → error (no re-sign in v1).
- [ ] Tests incl. state transitions; `make ci` passes.

### US-006: `pdf_signer` MCP App
**Description:** As the user, I want to review the exact filled PDF and sign
it by typing my name, so nothing is signed without my eyes on it.

**Acceptance Criteria:**
- [ ] New app `mcp_server/apps/pdf_signer/` (React + Vite +
      vite-plugin-singlefile, bun, pdf.js viewer reusing the Inbox pattern);
      `dist/mcp-app.html` committed; wheel force-include entry added to
      `pyproject.toml`.
- [ ] Renders all pages with filled values visible; signature placement
      highlighted; user must be able to reach every page (scroll/paginate)
      before the Sign button row.
- [ ] Ceremony inputs: text box "Sign as (full legal name)" + required
      checkbox "I agree this constitutes my electronic signature". Sign
      button disabled until both present.
- [ ] Sign click → app-only `pdf_signer.sign`; success state shows
      "Signed by {name} · {date}" badge; cancel/error states rendered.
- [ ] vitest suite incl. `appContract.test.ts` pinning the ext-apps `App`
      surface (repo convention); `make ci` passes.
- [ ] Verify rendered app manually via `make dev_host` (dev-browser
      equivalent for MCP apps).

### US-007: App-only tools + `sign` implementation
**Description:** As the system, I must produce the signature server-side
with layered guarantees that no model ever signs.

**Acceptance Criteria:**
- [ ] `mcp_server/app_tools/pdf_signer.py` with `_APP_META` visibility
      hint (repo convention) and `guard_user_id`:
      `pdf_signer.get_document(doc_id)` (bytes for the iframe) and
      `pdf_signer.sign(doc_id, typed_name, consent)`.
- [ ] `sign` enforcement order: (1) doc status must be
      `awaiting_signature`; (2) `consent` must be true and `typed_name`
      non-empty; (3) when the client supports elicitation, elicit
      "Sign {filename} as '{typed_name}'?" and abort (back to
      `awaiting_signature`... remains) on decline/cancel; (4) only then sign.
- [ ] Signing = visible stamp at the recorded placement (typed name in an
      italic face + printed name + ISO date; REVISED for v1: the open
      question below resolved to standard-14 Helvetica-Oblique instead of a
      bundled script font - zero licensing surface, renders everywhere),
      audit trail
      embedded in the PDF (typed name, UTC timestamp, SHA-256 of the
      pre-signature bytes, consent, elicitation-confirmed flag) and stored
      in the `audit` column, then PAdES seal via pyHanko with the server
      certificate; status → `signed`.
- [ ] Server certificate: path from config; if unset in dev, a self-signed
      cert is generated once and cached under the config dir; prod expects a
      real cert (documented).
- [ ] Signed docs are immutable: `pdf_edit` and `pdf_request_signature`
      reject; seal verifies in a PDF validator (pyHanko `validate` in tests).
- [ ] Failed/aborted attempts recorded in the audit log with reason.
- [ ] Tests: full sign path with elicitation stub (accept + decline),
      state-machine rejects, seal validation; `make ci` passes.

### US-008: Elicitation fallback for no-app hosts
**Description:** As a user on a host without iframe support (e.g. a CLI
host), I can still sign - by typing my name into the host's native
elicitation dialog.

**Acceptance Criteria:**
- [ ] In the `pdf_request_signature` enhancer: if `can_show_app` is false
      and `can_elicit` is true, elicit a flat schema `{full_name, consent}`;
      on accept, run the same server-side sign path (same audit + seal, with
      `channel: "elicitation"` recorded); on decline, doc returns to `open`.
- [ ] If neither apps nor elicitation available → result
      `signing_unavailable` with a human-readable explanation for the model
      to relay. Filling still works everywhere.
- [ ] MCP e2e test covers the fallback and unavailable paths; `make ci`
      passes.

### US-009: `pdf_export` - server-side attach to Gmail draft
**Description:** As the host LLM, I want the signed (or filled) PDF attached
to a Gmail draft without the bytes entering my context.

**Acceptance Criteria:**
- [ ] `@service pdf_export(doc_id, destination)` with `destination` union -
      v1 variant `gmail_draft {draft_id, filename?}`; `mutating=True`.
- [ ] Implementation delegates to the existing attachment machinery through
      `services/pdf_gmail_bridge.py`; returns the draft's attachment list
      (same shape as `gmail_add_attachment`), never `data_base64`.
- [ ] Default filename `{original-stem}-signed.pdf` when status is `signed`,
      `{original-stem}-filled.pdf` otherwise.
- [ ] Exporting an `awaiting_signature` doc → error (finish or cancel
      signing first).
- [ ] Tests with Gmail client stubbed; `make ci` passes.

### US-010: Isolation contract + registration + docs
**Description:** As a maintainer, I want the PDF domain mechanically
prevented from coupling to Gmail, and the feature documented.

**Acceptance Criteria:**
- [ ] `.importlinter` forbidden contract: PDF core modules
      (`services.pdf_forms_svc`, `services.pdf_signing`,
      `models.pdf_forms`, `mcp_server.app_tools.pdf_signer`,
      `mcp_server.enhancers.pdf_forms`) may not import `services.gmail_*`,
      `services.pdf_gmail_bridge`, or `models.gmail`; `make import_lint`
      passes.
- [ ] Enhancer imports registered in `_register_tools()`
      (`mcp_server/server.py`); new config block `pdf_forms.yaml` in
      `common/`.
- [ ] `MCP_UI_EDGE_CASES.md` gains an entry: residual risk when a host
      neither hides app-only tools (A4) nor supports elicitation - a model
      could submit a typed name; mitigations (state machine, audit trail,
      seal) documented.
- [ ] Docs page under `docs/content/en/` describing the flow + security
      model; `make ci` (incl. `docs_lint`) passes.

## 5. Functional Requirements

- FR-1: The system must expose exactly four LLM-visible PDF tools:
  `pdf_open`, `pdf_edit`, `pdf_request_signature`, `pdf_export`.
- FR-2: `pdf_open` must accept a source union (v1: Gmail attachment
  locator), create a `doc_id` session, and return field inventory and - for
  flat PDFs - text layout with page coordinates.
- FR-3: `pdf_edit` must apply a batch of `set_field` / `add_text` operations
  atomically and reject edits on `awaiting_signature`/`signed` documents.
- FR-4: `pdf_open` and `pdf_edit` must support `render_pages`, returning
  rasterized pages as MCP `ImageContent` on MCP and base64 elsewhere.
- FR-5: `pdf_request_signature` must transition `open →
  awaiting_signature`, record the placement, and attach the signing app when
  the host supports MCP Apps.
- FR-6: No LLM-visible tool may create, modify, or apply a signature. The
  only signing entry point is app-only `pdf_signer.sign`, or the elicitation
  fallback driven inside the enhancer.
- FR-7: `pdf_signer.sign` must require typed name + consent, verify document
  state, and - when the client supports elicitation - obtain a host-native
  confirmation naming the document and typed name before signing.
- FR-8: Signing must stamp a visible signature (script-font typed name +
  printed name + date), embed the audit trail (name, UTC timestamp,
  pre-signature SHA-256, consent, channel), and apply a PAdES seal with the
  server certificate.
- FR-9: Signed documents must be immutable within the system, and any
  post-seal modification must be detectable by standard PDF validators.
- FR-10: PDF bytes must never appear in any LLM-visible tool result;
  transfer into the iframe uses app-only tools; transfer to email uses
  `pdf_export` server-side.
- FR-11: On hosts without apps but with elicitation, signing must fall back
  to a host-native typed-name prompt; with neither, signing must return
  `signing_unavailable` while filling continues to work.
- FR-12: Gmail knowledge must be confined to `services/pdf_gmail_bridge.py`,
  enforced by import-linter.

## 6. Non-Goals (Out of Scope)

- **No per-user X.509 certificates** - the seal uses one server-held cert
  (DocuSign-style platform seal).
- **No drawn (canvas) signatures** - typed name only in v1.
- **No multi-signer routing/envelopes** (send-to-counterparty-for-signature
  workflows). One signer: the current user.
- **No sandbox self-editing path / presigned upload-download URLs** -
  evaluated and rejected for v1: ChatGPT web and Claude web sandboxes have
  no network egress and no MCP-result-to-file bridge; base64 through model
  context is corruption-prone and can exceed context limits. Possible v2
  escape hatch for agentic hosts.
- **No non-Gmail sources/destinations in v1** (uploads, URLs, filesystem) -
  the source/destination unions and bridge seam make these additive later.
- **No re-signing or signature revocation** in v1; signed docs are terminal.
- **No legal-compliance certification** - the design follows ESIGN/UETA
  e-signature practice (intent + attribution + record + tamper evidence)
  but nothing here is legal advice.

## 7. Technical Considerations

- **Dependencies (license-vetted):** `pypdf` (BSD) for AcroForm
  inspect/fill and annotations; `pyHanko` (MIT) for the PAdES seal;
  `pypdfium2` (Apache/BSD) for rasterization. **PyMuPDF is explicitly
  avoided (AGPL).** All added via `uv add`.
- **Security model (three layers, strongest first):** (1) server state
  machine + no LLM-visible signing tool; (2) elicitation confirmation
  rendered by the host outside the iframe - a model cannot answer it, a
  spoofed iframe cannot fake it; (3) `visibility=["app"]` hint. Residual
  risk (host honors neither) documented per US-010.
- **Doc sessions follow the repo's long-running pattern**
  (`init → continue(id) → cleanup(id)`): `pdf_open` is init, `pdf_edit` /
  `pdf_request_signature` continue, `pdf_export` + a retention sweep
  (config `pdf_forms.session_ttl_hours`) are cleanup. State fully
  serializable in `pdf_documents`.
- **Reuse:** pdf.js viewer pattern from `mcp_server/apps/gmail_inbox`;
  enhancer `ImageContent` pattern from `mcp_server/enhancers/gmail_attachment.py`;
  app-only auth from `mcp_server/app_tools/_auth_guard.py`; attachment
  rebuild from `services/gmail_attachments_svc.py` (via the bridge).
- **MCP spec volatility:** verify elicitation and Apps (`_meta.ui`) shapes
  against the current spec during implementation (per repo policy), not
  training data.
- **Coordinates:** PDF user-space (origin bottom-left, points). `pdf_open`'s
  `text_layout`, `pdf_edit`'s `add_text`, and the signing placement all use
  the same convention; document it in every tool description.

## 8. Success Metrics

- The NDA scenario completes with ≤ 3 user chat inputs (missing field
  values, name-typing ceremony, send confirmation) and zero manual PDF
  editing.
- 100% of signatures in test and manual runs carry: typed name, consent,
  timestamp, doc hash, and a pyHanko-validatable seal.
- Zero LLM-visible tool responses contain PDF bytes (asserted in e2e tests).
- Post-signature tampering is detected by `pyhanko validate` in the e2e
  suite.
- `make import_lint` proves the PDF core has no Gmail imports.

## 9. Open Questions

- Signature *appearance* font: bundle an OFL script font vs. draw the name
  with a standard PDF font in italic - decide during US-007 (bundling an
  OFL font is the current lean).
- Should `pdf_export` also support "download link in the signing app" for
  hosts where the user wants the file locally? (Leaning v2.)
- Retention: is a TTL sweep enough, or should `pdf_export` offer
  `close: true` to delete the session eagerly? (Leaning TTL-only for v1.)
- Flat-PDF signature placement UX: v1 lets the LLM propose `{page, x, y}`
  and the user sees it highlighted in the app - is a drag-to-reposition
  control in the signing app worth it? (Leaning v2.)
