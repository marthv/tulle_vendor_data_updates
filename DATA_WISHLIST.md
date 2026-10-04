# Data wishlist: things couples ask about that we can't answer well yet

Started 2026-10-04. A running list of venue facts to capture in future scraping and extraction runs, so that
the Tulle Assistant (and filters) can answer them. Add to it whenever the assistant gets a question it can't
answer from our data.

**Status key:** HAVE = in our data and searchable today · PARTIAL = in our data but not searchable, or patchy
· NONE = we don't capture it.

**Rule for anything we add:** store "venue says yes", "venue says no" and "unknown" separately. Never treat a
missing fact as "no", and never say a venue is LGBTQ+ friendly or accessible unless the venue itself
(or a named source) says so.

## These need a re-scrape of the PDFs themselves

We can't backfill most of this from what we already store. Each PDF is kept only as an LLM summary plus the
pricing rows, not as full text, and the summaries leave out policies like caterer rules, fire, rooms and access
(user, 2026-10-04). Getting these facts means re-reading the original PDFs with the new fields added to the
extraction prompt. The plan already on the table: re-extract the top 2,000 venue PDFs (80% of clicks), pilot 50
first, est. $100-300. Decide the field list from this file before that run, so we pay for one pass, not several.
Worth also storing the full PDF text in that run, so the next new field doesn't need another re-scrape.

## Where each fact could come from

| Source | Good for | Notes |
|---|---|---|
| Pricing PDFs (existing extraction) | food rules, outside caterer fees, end times, multi-day pricing, fire/candle rules | We only store LLM summaries, not the raw PDF text, so every new field needs an extraction pass. Cheapest is to add these fields to the next routine extraction pass rather than a separate re-run. |
| Venue websites (FAQ / policies pages) | inclusion statements, accessibility, cultural ceremony experience | Needs a new scraper. |
| Google Places (we already store Place_IDs) | wheelchair access: entrance, parking, restroom, seating | Places API (New) has `accessibilityOptions` with these four fields. We already store Place_IDs (Place_ID backfill), so this is likely the cheapest source on the list. |
| Couples (PDF submissions, reviews, assistant chats) | lived experience ("they let us do a baraat") | The best signal, but needs a moderation step. |

## 1. Cultural and religious weddings

Demand so far (measured 2026-09-27): 15 of 20,498 vendor searches (0.07%) had clear culture intent (Indian
wedding 7, kosher 4, halal / chuppah / church / Chinese 1 each). This probably understates demand, because people
don't type it into a vendor search box. Assistant chats will be a better signal.

| Fact | Status today |
|---|---|
| Outside caterer allowed (needed for kosher, halal, Indian and other cultural cuisine) + fee | PARTIAL: "outside catering" is mentioned in ~1,737 venue PDFs (~15%), but not as a field |
| All-inclusive vs bring-your-own (`Venue_Offering`) | HAVE (99% filled; the assistant can filter on it) |
| Kosher: in-house, kosher kitchen, or approved outside kosher caterer | PARTIAL: mentioned in 31 PDF summaries, not a field |
| Halal catering | NONE (0 PDFs) |
| Vegetarian / Jain menu options | NONE |
| Open flame allowed (havan / mandap fire, unity candles, sparklers) | NONE |
| Chuppah / mandap / altar setup allowed or provided | NONE |
| Baraat or procession (horse, vehicle, drums) allowed on the grounds | NONE |
| Tea ceremony, lion dance, other ritual space | NONE |
| Multi-day events (mehndi, sangeet, welcome dinner) + multi-day pricing | NONE |
| Late end time / extended hours + overtime cost | NONE |
| Large guest counts (300-500+) | HAVE (`Max_Capacity_Seated`, 71% filled) |
| Dry wedding: food minimum without a bar package | NONE |
| Throwing rice, petals or confetti allowed | NONE |
| Noise / amplified music limits (dhol, live band) | NONE |
| Outside decorator allowed + fee | NONE |
| Prayer room or quiet room on site | NONE |
| Religious venue rules: own clergy required, members only, faith requirement | NONE |
| Saturday-after-sundown / religious-holiday date pricing | NONE |

## 2. LGBTQ+ friendly

| Fact | Status today |
|---|---|
| Venue's own inclusion statement (website, contract wording) | NONE |
| Listed in an LGBTQ+ wedding directory | NONE |
| Religious venue restrictions on same-sex ceremonies | NONE (matters most for churches and other faith venues) |
| Gender-neutral paperwork ("couple", not only "bride/groom") | NONE |
| Two getting-ready suites | NONE |
| All-gender restrooms | NONE |

## 3. Accessibility and accommodations

| Fact | Status today |
|---|---|
| Wheelchair-accessible entrance, parking, restroom, seating | NONE (Google Places field available; see sources) |
| Step-free route to ceremony and reception spaces; elevator to every event floor | NONE |
| Outdoor ground type (grass, gravel, hills, stairs) | NONE |
| Accessible getting-ready suite and on-site lodging | NONE |
| Accessible shuttle / drop-off point | NONE |
| Hearing loop or good mic / sound for the ceremony | NONE |
| Quiet or sensory room | NONE |
| Service animals welcome | NONE |
| Ramp to stage or dance floor | NONE |

## 4. Overnight accommodations (user ask, 2026-10-04)

Today: lodging is only captured indirectly. The extraction prompt (`extract_core.py`) tags a venue
"Hotel / Resort" when it is *lodging-first*. Barns, estates and wineries that also have cottages or guest
rooms are not flagged, and there is no room count anywhere.

| Fact | Status today |
|---|---|
| On-site overnight rooms (yes / no / unknown) | PARTIAL: only via venue type "Hotel / Resort" |
| Number of rooms AND number of people it sleeps (separate fields: "12 rooms" and "sleeps 40" answer different questions) | NONE |
| Rooms included in the rental vs. extra cost, and the price | NONE |
| Required minimum stay or buyout (e.g. "must book all rooms for 2 nights") | NONE |
| Hotel room block / discounted rate for guests | NONE |
| Nearby hotels and shuttle | NONE |

Before trusting it as a field: hand-check a sample of real PDFs for how they state rooms and capacity.

## 5. Other gaps the assistant has hit (2026-10-04)

| Fact | Status today |
|---|---|
| Outdoor ceremony space | PARTIAL: `Outside_Ceremony_Space` is 74% filled in table 36, but the assistant's venue search can't filter on it yet |
| Courtyard (and other features not on our 10 style tags) | NONE |
| Ceremony fee included in the rental | PARTIAL: ceremony fee is a pricing line, not a filter |
| Reviews / ratings | PARTIAL: Google rating cached; not given to the assistant |
| Date availability | NONE (vendor calendar work is separate) |
| Parking, lodging, rain plan | NONE |

## How to decide what to build first

Measure demand before scraping. Count assistant questions (rec_messages, table 43) that mention each topic
once the assistant has a few weeks of real use, and optionally add a "traditions / needs" multi-select to
onboarding. Build the fields that couples actually ask about.

As of 2026-10-04 there are only 42 questions in table 43, mostly from test accounts, so there is nothing to count
yet. Once there are a few hundred real questions, add an "asked" count per row here (or move this list to a Xano
table if re-ranking by hand gets tedious). Choose which fields go into the planned top-2,000 venue PDF
re-extraction (pilot 50 first, est. $100-300) from the top unanswered rows.
