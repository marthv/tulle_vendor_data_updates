---
name: feedback_evidence_and_intention
description: State your evidence before recommending anything; quantify who a change helps before building it. Set 2026-09-12 after shipping a fix on round-trip evidence alone.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1c55a874-744a-4113-9ca4-188a29690ccd
  modified: 2026-09-12T15:56:25.205Z
---

Set by the user 2026-09-12, in their words: **"i want to make sure we're not building just to
build. i want to build with intention and purpose and to make the customer experience a lot
better."** Also written into `tulletogether/CLAUDE.md` §12 and
`tulle_vendor_data_updates/CLAUDE.md` §2 — this memory is the version that survives outside
both repos.

## 1. State your evidence before recommending anything

"It parsed", "it round-tripped", "the API returned 200", "it looks right" are **NOT evidence
that a change works.** Label those **UNVERIFIED**, and say what would verify it and when.

These are four separate claims, each needing its own evidence:
landed → runs → does something → does something users notice.

If a verification attempt is **inconclusive, say so** — do not quietly retreat to the weaker
claim.

## 2. Don't propose a fix you're below ~90% confident in

Below that, don't present it as a recommendation. Say the confidence is low and what would
raise it. An honest "I don't know yet" beats a confident-sounding guess.

The user's own framing of why the process rule beats a confidence rule: a calibration
threshold isn't a hard gate because the confidence estimates aren't well calibrated. The
enforceable version is *state the evidence*, which is checkable.

## 3. Build with intention — quantify the benefit BEFORE building

State **who it helps and by how much, in measured terms**, before proposing work.
- Can't quantify it? Say so.
- Strictly better but users won't notice? That's a **cleanup** — label it, don't dress it up.
- Say when you're proposing the smaller change only because the bigger one is blocked.
- Volume of work is not value.

## 4. Correct your own numbers loudly

Old value, new value, what caused the error — prominently, not buried later. Numbers here
drive real decisions about real money ($183/day, 50k users, solo operator).

## Why — the failure that prompted this

The ep119 cache-warmer realignment ([[project_perf_baseline_digest]]). I reported it as done
and worthwhile when the only evidence was that it round-tripped into the run stack. Then:

- The smoke test I wrote afterwards returned **401** — but it was testing the *harness*, not
  the change: `security.create_auth_token` under `runWorkspaceFunction` mints a token ep119
  rejects, while the same call in **task** context gets 23/23 × 200. Inconclusive, not proof
  either way.
- The payoff was ~**24 cold searches/day out of ~102 misses**, and could never move p95,
  because 78 of those misses are single-visitor keys no warmer can reach. "Worthwhile" was
  doing more work than it had earned, and that should have been said **before** shipping.

The user asked "are you absolutely sure this one works and is worthwhile?" — the honest
answer was no on both counts, and I should have volunteered that first.

## Also true, and worth not over-correcting into

This is not an instruction to hedge everything or to stop acting. Ordinary work still gets
done without ceremony. The rule is about **labelling the evidence honestly** and **sizing the
benefit before building**, not about adding caveats to everything.
