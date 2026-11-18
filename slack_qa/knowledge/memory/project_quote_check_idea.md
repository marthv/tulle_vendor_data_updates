---
name: project_quote_check_idea
description: "Is this quote a deal?" upload feature (team idea 10-14) - latency MEASUREMENT approved 10-14; no website change until user launches it
metadata:
  type: project
---
Team idea (2026-10-14, pasted by user): couples upload a vendor quote and get an instant read vs the market ("am I getting ripped off?"), maybe 3 free checks. Rationale: our data is the moat one-off AI agents (e.g. Meta's Muse) can't replicate. User asked whether real-time extraction + whole-DB comparison means too much latency.

My answer: latency = ONE single-PDF extraction (~20-60s, UNVERIFIED until timed) + lookup against PRE-COMPUTED bands (PI tables 62/63 / ep230-231, P/E benchmarks; <1s) + short verdict call. Real risk is accuracy (packages, minimums, guest_min, space grain), not speed. Every uploaded quote is also new pricing data (needs consent + confidentiality flagging, see [[project_confidentiality_flagging]]).

**10-14 final:** user: measure the latency (OK, agent running), but NOTHING changes on the website yet - no fake-door CTA; all quote-check launch items come later together. Proposed if revived: (1) time 5-10 real quote PDFs through single-pass extraction (~$1-2); (2) AFTER the free-assistant publish, a fake-door "Check if your quote is a good deal" CTA (vendor page + assistant) that tracks taps and collects email/uploads. Natural home if it works: inside Tulle Assistant, 3 free checks. Kept out of the 10-14 publish on purpose.

**Why:** can't size demand (no data on whether couples hold quotes while on Tulle) - rule 12.3.
**How to apply:** don't build the full feature until both tests read out. Related: [[project_ai_recommendation_engine]], [[project_pi_band_ref_v20]].

## 10-14 latency measured (subagent, ~$0.72, 7 PUBLIC web pricing PDFs - Drive corpus download is restricted)
- Single pass, prod prompt (extract_core SUMMARY_PROMPT, claude-sonnet-4-6): median 14.9s, worst 22.6s (n=7, 1-8 pages, up to 7.9 MB), ~$0.03-0.08/PDF. Page count did not drive time.
- 3-pass (summary + pricing grid + Haiku): 35-53s sequential ($0.09-0.23). Sonnet 5.5: 5.5-12s but prompt-cached (cold UNVERIFIED); extract_core.call_claude reads content[0].text and BREAKS on 5.5 (thinking block first).
- Lookups: ep231 band 0.35-0.76s miss / 0.17-0.26 hit; ep230 0.4-0.5s; ep207 P/E 0.2-0.3s. BUT all keyed by vendor_id - NO endpoint gives a band for an arbitrary uploaded quote (state/capacity/package) -> that's real build work.
- Couple wait ~15-24s single pass. Accuracy is the risk: single pass dropped Fri/weekday tiers, budgeting guidance stored as price, packages collapsed, CAD not flagged, combined fee double-counted. 3-pass grid caught day tiers.
- Files: session scratchpad results.jsonl / lookup.json / out_*.json.
