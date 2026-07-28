# Pricing strategy

How DaySurface monetises without gating the thing that makes it worth adopting.

Status: proposal. Nothing here is implemented yet beyond the daily-quota
scaffolding already in `api_server/billing/`.

## 1. The constraint that decides everything else

DaySurface is MIT-licensed and self-hostable. That means:

> You cannot sell the code. You can only sell **your instance of it**, plus the
> things an organisation needs that a solo self-hoster does not.

Any entitlement check we add is deletable in four lines by anyone who clones
the repo, and that is fine. It is not a leak, it is the deal. The practical
consequences:

- Entitlement enforcement must be **off by default** and switched on only in
  the hosted deployment (`production_config.yaml`). A self-hoster gets every
  feature. Shipping a crippled OSS build to protect hosted revenue would poison
  the distribution channel that makes the hosted product findable at all.
- Never gate anything whose only cost is "we wrote the code". Gate things whose
  cost is **ongoing** (our servers keep working while the user sleeps) or whose
  value is **organisational** (a solo user literally cannot use it).
- The OSS repo is the top of funnel. Every free-tier restriction is a tax on
  the funnel, so each one has to earn its place.

## 2. The framework: three axes, not a feature list

Picking features to paywall one at a time produces an incoherent tier that
customers argue with. Use three axes instead. A feature is paid if it sits on
one of them, free otherwise.

```
                        FREE                         PAID
                 ------------------          ---------------------
  AUTONOMY       You are in the chat.   -->   It runs while you sleep.
                 Read, search, triage,        Watches, webhooks, background
                 draft, send, archive.        follow-up, scheduled rules.

  ORGANISATION   One person, one         -->  More than one of you.
                 mailbox, one machine.        SSO/SAML, SCIM, seats, shared
                                              rules, audit log, admin console.

  UNIT COST      Bounded per-call work.  -->  Metered beyond a free allowance.
                 Gmail API passthrough,       Retained history, PDF bytes,
                 stateless computation.       webhook deliveries, LLM spend.
```

The one-line version, and the one to put on the pricing page:

> **Interactive is free. Autonomous is paid. Scale when you need to.**

This is defensible in public, it maps exactly onto our cost curve, and it
answers "why is X paid?" without special pleading in every case.

### What stays free, permanently, no asterisk

The Gmail-parity core: `gmail_list_inbox`, `gmail_get_thread`, `inbox_search`,
`gmail_compose`, `gmail_send`, `gmail_reply_to_thread`, `gmail_update_draft`,
`gmail_archive_thread`, `gmail_mark_thread_done`, `gmail_curate_inbox`,
`inbox_get_curation`, `inbox_save_curation`, `gmail_connect`, `doctor`.

That is the promise: everything you can do in the Gmail web UI, you can do
through the agent, for nothing. Write it into the README so future-us cannot
quietly walk it back.

## 3. Verdict on the five candidate gates

| Idea | Verdict | Shape |
| --- | --- | --- |
| >30-day data access | **Yes, reframed** | Ratchet |
| Support | Yes, but not tier-defining | Line item |
| SSO / SAML | Yes, $300/mo add-on, self-serve | Hard gate |
| PDF signing | Yes, but **meter it, do not gate it** | Meter |
| Follow-up Manager | **Yes. Strongest candidate.** | Hard gate on a new feature |

### 3.1 "More than 30-day data access" needs renaming

Read literally this is wrong, and the wrongness matters. The mail lives in
Gmail. We do not hold it, and `inbox_search` is a passthrough to Gmail's own
index. Blocking a search for a two-year-old thread would break the free
Gmail-parity promise, and users would (correctly) call it hostage-taking of
data we do not even store.

What we *do* store, and can legitimately price, is DaySurface-derived state:

- `thread_curation`: the banked LLM triage ledger. This is the expensive
  artifact. It cost real LLM tokens to produce and it is what makes repeat
  "what's important?" reads nearly free.
- `pdf_documents`: original plus current bytes, per document session.
- webhook events and delivery history.
- follow-up state, once it exists.

So the gate is **memory and history retention**, not "data access":

> Free: 30 days of curation history, PDF sessions and event history.
> Paid: unlimited retention.

This is honest, it tracks a real storage bill, and it is the single best-shaped
paywall we have, for the reason in section 4.

### 3.2 PDF signing: meter, do not gate

Signing is a wow moment and one of the few things that will make someone tell a
colleague. Hard-gating it means nobody discovers it and it converts nobody.

Meter it instead: **3 completed signature ceremonies per month free**, then
upgrade. By the time a user hits the wall they have successfully signed three
documents, so the pitch is "keep doing the thing that already worked", not
"pay to find out if this works".

Check the entitlement at `pdf_open`, never at `pdf_export`. See section 4.4.

### 3.3 Follow-up Manager is the strongest paid feature we have

It does not exist in the codebase yet (there is a `follow-ups` skill, no
service). Build it paid from day one, because:

- Gmail does not do this, so gating it does not gate the core promise. Nobody
  can say we took something away.
- It needs server-side state plus a background scheduler. It is pure ongoing
  cost, squarely on the autonomy axis.
- It is exactly the business value the paid tier is supposed to be about:
  deals, replies owed, things slipping.

Do not ship a free version first and claw it back later. That is the one
sequencing mistake in this whole plan that cannot be undone.

### 3.4 SSO/SAML

Paid, no negotiation, and WorkOS is already wired
(`api_server/auth/workos_auth.py`, `authkit_auth.py`), so the lift is small.

One caution: do not make SSO a "call sales" tarpit. The SSO-tax backlash is
real and it is worse for an OSS project, where the audience is exactly the
crowd that writes the blog posts. The fix is a **published price you can pay
without a sales call**, not necessarily a cheap one: enterprise SSO lands in
the $300/mo Governance add-on (5.1), while ordinary Google sign-in stays free
on every tier. Enterprise custom pricing is for SLA, DPA, and dedicated infra,
not for turning on a login method.

### 3.5 Missing from the list, and worth more than some of what is on it

- **Webhooks and background watch** (`webhook_subscribe`, `gmail_watch_start`,
  and friends). This is the purest autonomy feature in the repo today and it is
  currently free. Real-time push, Pub/Sub, a delivery worker with retries: it
  is the most expensive thing we run per user. Paid.
- **Multiple connected accounts.** Free is one mailbox. Painless, standard, and
  it maps neatly to "you now have a work inbox and a personal inbox".
- **Shared team curation.** Team-wide rules and importance priors, shared
  mailbox triage. Team.
- **Audit log and retention policy controls.** Team and above.
- **Team size.** Bundled rather than sold per seat, so the price never
  becomes an argument about headcount.
- **Throughput.** Tool calls, concurrent sessions, and webhook deliveries are
  the one thing that scales linearly with our bill, which makes them the
  honest basis for a tier above Team.

## 4. The paywall user journey

This is the part most MCP products get wrong, because the paywall does not live
on a web page. It lives **inside a conversation with an agent**. A bare HTTP 402
mid-conversation does not read as "upgrade to continue". It reads as *the
product is broken*, and the agent will often say so out loud, then try
something else, then apologise. We get churn and a bad transcript instead of a
sale.

### 4.1 Three wall shapes, in order of preference

```
  RATCHET   value accrues, then ages out        <- best
            "your curation history older than 30 days has been trimmed"
            User has already had 30 days of value. Loss is felt, not imposed.

  METER     N free uses, then upgrade
            "3 of 3 signatures used this month"
            User has succeeded N times. Pitch is continuation, not risk.

  GATE      hard stop, feature unavailable      <- only for org-shaped things
            "SSO requires the Governance add-on"
            Fine here: whoever hits this is a buyer, not a user. It is a
            sales conversation, not a paywall.
```

Never put a hard gate on the autonomy axis for a first-time user. Gate the
*second* webhook subscription, not the first.

### 4.2 Value before wall: the concrete sequence

```
  day 0    connect Gmail, triage inbox, send replies      FREE, no signup wall
  day 0-30 curation ledger accumulates, gets better       FREE
           first PDF signed                               FREE (1 of 3)
           first webhook subscription                     FREE (1 of 1)
  ~day 7   agent mentions Follow-up Manager exists        soft, in-chat
  day 31   oldest curation ages out                       RATCHET fires
           "Team keeps this. Try free for 14 days?"       one click, in chat
```

The free tier *is* the trial. The `trial_period_days: 7` currently in
`common/subscription_config.yaml` is the wrong shape for a 30-day ratchet:
it expires three weeks before the user ever feels the wall.

Recommendation: no card-up-front trial at signup. Instead offer a **14-day Team
trial triggered at the moment of the wall**. Intent is proven at that instant,
so it converts far better than a signup-time trial that most people forget they
started.

### 4.3 Rules for an in-chat paywall

1. **The error must carry a checkout URL.** We already have the perfect
   precedent: `ConnectRequiredError` in `services/__init__.py` converts into a
   SEP-1036 URL elicitation so capable hosts open the consent flow natively.
   Add `UpgradeRequiredError` as a sibling with the same contract. One click
   in-chat, then the agent retries the tool. Never "log into the dashboard".
2. **The message must be a self-recovering script**, exactly as
   `ConnectRequiredError` already documents: what to do, with which URL, then
   retry. For hosts with no elicitation affordance the exception text is the
   only channel that reaches the user.
3. **Warn before the wall.** Put remaining allowance in the successful
   response's `_meta` so the agent can say "that is your second of three free
   signatures this month" in its own words. The agent becomes the upsell
   channel. No other product category gets this for free, and it is far less
   annoying than a banner.
4. **Keep gated tools in `tools/list`, prefixed in the description.** Hiding
   them means nobody ever learns Follow-up Manager exists. Prefix with `[Team]`
   so the agent can offer the feature without burning a failing call on it.
5. **Never break a mutating flow mid-flight.** Entitlement is checked at the
   entry of a multi-step flow, never at the exit. `pdf_open` checks; `pdf_edit`
   and `pdf_export` do not. A user who fills a twelve-field form and then hits
   a wall at export has lost their work and will not be back.
6. **Downgrade never locks a user out of their own data.** When a subscription
   lapses: writes and new automations stop, reads stay. Do not delete retained
   history on lapse, freeze it, and restore on resubscribe within a grace
   window. Locking someone out of their inbox tooling over an expired card is
   how an OSS project earns a front-page thread it does not want.
7. **Self-hosters see none of this.** With `enforce: false` there is no wall,
   no `[Team]` prefix, no upgrade elicitation.

### 4.4 Failure mode to design against

A user asks the agent "chase everyone who has not replied". The agent calls a
Team-gated follow-up tool, gets a hard 402 with no URL, decides the tool is
broken, falls back to reading 40 threads by hand, burns the user's context and
their patience, and never mentions that a paid feature would have done it in
one call. Everything in 4.3 exists to prevent that specific transcript.

## 5. Proposed tiers

Four rungs plus an add-on: Free, Team, Scaling, Enterprise, with governance
sold separately. Two structural decisions carry the whole shape.

**Scale is a tier; governance is an add-on.** These vary independently. A
six-person team that needs Okta should not have to buy throughput for
twenty-five people to get it, and a high-volume team of six should not need an
enterprise contract to raise a rate limit. Bundling them into one ladder forces
half your buyers to pay for something they will not use.

**An individual tier stays deleted.** A "Pro" between Free and Team existed
only to charge people who work alone for autonomy. Team is the first paid rung
whoever you are.

| | Free | Team | Scaling | Enterprise |
| --- | --- | --- | --- | --- |
| Price | $0 | $29/mo | $199/mo | Custom |
| Members | 1 | 5 incl, $6/mo after | 25 incl, $5/mo after | Unlimited |
| Tool calls / day | 500 | 5,000 | 50,000 | Custom |
| Concurrent sessions | 1 | 5 | 25 | Custom |
| Webhook deliveries | 100/day | 10k/day | 100k/day | Custom |
| Background queue | None | Standard | Priority | Dedicated |
| Gmail-parity core | Full | Full | Full | Full |
| Connected mailboxes | 1 | Unlimited | Unlimited | Unlimited |
| Curation retention | 30 days | Unlimited | Unlimited | Custom |
| PDF signing | 3/mo | Unlimited | Unlimited | Unlimited |
| Follow-up Manager | No | Yes | Yes | Yes |
| Shared rules, audit log | No | Yes | Yes | Yes |
| Enterprise SSO, RBAC | No | Add-on | Add-on | Included |
| Support | Community | Priority | Priority + target | Dedicated |
| Uptime SLA | No | No | No | Yes |
| Self-host | Everything, always, no limits | | | |

### 5.1 The Governance add-on, $300/mo

Attaches to Team or Scaling, self-serve, included in Enterprise:

- Enterprise SSO (Okta, Entra ID, any SAML IdP)
- SSO enforcement (block non-SSO sign-in org-wide)
- Fine-grained RBAC across mailboxes and rules
- Support via a dedicated Slack or MS Teams channel

Note this **moves SSO out of the $29 tier**, where an earlier draft of this doc
put it. The reasoning in 3.4 still holds, but it was aimed at the wrong target.
The thing that earns the backlash is *"contact sales to log in"* - an unpriced
gate that turns a login method into a procurement cycle. A published $300 price
that a team can put on a card without talking to anyone is not that. Ordinary
Google sign-in stays free on every tier including Free, so nobody is locked out
of authentication itself.

$300 is also honest about who it is for. Okta integration, SCIM provisioning,
enforcement policy, and a staffed Slack channel are real recurring cost, and
the buyer is a security team with a budget line, not an individual.

### 5.2 Notes on the numbers

- **The rate limits are placeholders.** 500 / 5,000 / 50,000 tool calls a day
  are round numbers picked to be legible, not calibrated against real usage.
  Before launch, instrument actual per-user call volume and set Free just above
  what an engaged daily user generates, so the free tier never feels like a
  trap, and set Team where continuous agent operation starts to bite.
- **$29 flat for five is deliberately generous** and it is the whole
  positioning. Five seats at a typical $30/seat would be $150/mo for the same
  thing. Undercutting that by 5x is a far stronger story than shaving features
  off the free tier.
- The cost of that: a solo user pays $29 rather than $20. Accepted, and
  answered directly in the pricing FAQ rather than left for people to work out.
- Extra members are $6/mo (Team) and $5/mo (Scaling) rather than another tier,
  so the ladder stays four rungs regardless of team size.
- Scaling sells headroom, not features. Keeping the feature set identical to
  Team is what makes the tier honest: nobody upgrades for a capability, they
  upgrade because they hit a ceiling they can see coming in the usage analytics.

## 6. Implementation design

Everything below slots into structure that already exists. No new architecture.

### 6.1 Where the checks go

There are exactly three chokepoints where an authenticated user meets a
service, and all three already call `ensure_daily_limit`:

```
   CLI (src/cli)           MCP (mcp_server)          HTTP (api_server)
        |                        |                          |
        |                  _tool_factory.py           routes/services.py
        |                  _check_quota()             routes/stream.py
        |                        |                          |
        |                        v                          v
        |            +-----------------------------------------------+
        |            |  api_server/billing/limits.py                  |
        |            |  ensure_daily_limit(user_id)                   |
        |            |                                                |
        |            |  NEW: entitlements.py                          |
        |            |    ensure_entitled(user_id, entry)             |
        |            |    consume_meter(user_id, meter, n=1)          |
        |            +-----------------------------------------------+
        |                        |
        |                        v
        |            db.user_subscriptions (tier, status, period)
        |            db.usage_meters       (NEW: per-meter counters)
        |
        +--> no auth bound, no user, no gate. CLI stays free forever.
```

Three call sites to touch. That is the whole enforcement surface, and it is why
this is cheap to add.

### 6.2 Declare entitlements on the service, not at the call site

Extend the `@service` decorator (`services/__init__.py`) with one field:

```python
@dataclass
class ServiceEntry:
    name: str
    description: str
    input_model: type
    output_model: type
    func: Callable[..., Any]
    mutating: bool = False
    tier: str = "free_tier"  # NEW: minimum tier
    meter: str | None = None  # NEW: metered allowance key
```

Then a gated service is a one-line change and every transport inherits it:

```python
@service(
    name="followup_list",
    description="...",
    input_model=FollowupListInput,
    output_model=FollowupListResult,
    tier="team_tier",
)
```

This mirrors how `mutating=True` already propagates to REST idempotency and the
MCP crash-fallback. Same pattern, same place, nothing new to learn.

### 6.3 Enforcement is config-driven and off by default

In `common/subscription_config.yaml`:

```yaml
entitlements:
  # Self-hosters get everything. The hosted deployment flips this to true
  # via production_config.yaml. Never default this to true.
  enforce: false
tier_limits:
  free_tier:
    daily_requests: 500
    retention_days: 30
    connected_accounts: 1
    meters:
      pdf_signatures: 3
      webhook_subscriptions: 1
  team_tier:
    daily_requests: 5000
    retention_days: 0        # 0 = unlimited
    connected_accounts: 0    # 0 = unlimited
    included_members: 5      # extra members billed per-seat on top
    meters: {}
  scaling_tier:
    daily_requests: 50000
    retention_days: 0
    connected_accounts: 0
    included_members: 25
    meters: {}
  enterprise_tier: ...
# Add-ons are entitlements too, granted independently of the tier.
add_ons:
  governance:
    grants: [sso_saml, sso_enforcement, rbac, dedicated_channel]
```

`SubscriptionTier` in `db/models/subscription_types.py` grows `TEAM`,
`SCALING`, and `ENTERPRISE`. Add-on grants live in a separate table rather than
on the tier enum, since they are orthogonal to it. Keep `PLUS = "plus_tier"` as a deprecated alias so
existing rows keep resolving; map it to `team_tier` limits.

### 6.4 UpgradeRequiredError, modelled on ConnectRequiredError

```python
class UpgradeRequiredError(Exception):
    """A service call requires a higher tier than the user's current one.

    Same contract as ConnectRequiredError: `message` must be a
    self-recovering script (what this needs, where to upgrade, then retry),
    because for hosts with no elicitation affordance the exception text is
    the only channel that reaches the user.
    """

    def build_checkout_url(self) -> str | None: ...
```

MCP converts it via the existing `reraise_with_elicitation` path in
`mcp_server/_tool_factory.py`. HTTP maps it to 402 with the checkout URL in the
body, alongside the existing `quota_exceeded` shape in `limits.py`. CLI never
raises it.

### 6.5 Retention is a job, not a check

Retention needs a nightly sweep (`tasks/`) that trims `thread_curation`,
`pdf_documents` and webhook events past the user's `retention_days`, skipping
users where it resolves to 0. Note that `services/webhooks_svc.py` already has
`purge_user_events`, so there is precedent for the delete path.

Freeze rather than delete on lapse: add `frozen_at` and only hard-delete after a
grace window (30 days is fine, and it matches the free retention number).

## 7. Sequencing

1. **Entitlement plumbing** (`tier=` / `meter=` on `@service`, `entitlements.py`,
   `UpgradeRequiredError`, the three call sites, `enforce: false`). Ships dark,
   changes nothing for anyone.
2. **Follow-up Manager, paid from day one.** The one thing that must never ship
   free first.
3. **Retention ratchet plus the nightly job**, with in-chat warning at day 23.
4. **PDF signing meter** at 3/month, plus `_meta` allowance reporting.
5. **Webhooks and background watch** moved to Team, with the first subscription
   free.
6. **Team features**: member management, shared rules, audit log, self-serve
   checkout.
7. **Scaling tier + the Governance add-on**, once real usage data exists to
   calibrate the rate limits in 5.2.
8. Reposition the landing page around autonomy rather than "we host it".

Steps 1 to 4 decide whether this monetises at all. Steps 5 to 8 decide whether
it monetises to businesses.
