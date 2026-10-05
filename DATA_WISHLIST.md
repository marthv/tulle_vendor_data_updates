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
extraction prompt. The plan already on the table: re-extract the top 3,000 venue PDFs by clicks (user, 2026-10-04; earlier
drafts said 2,000 and 5,000), PLUS a coverage floor: at least 10 venues from every state and country, taking
the most-clicked ones there; if a state or country has fewer than 10, take all of them. The top 3,000 alone
would skip the smaller states, and the assistant needs some coverage everywhere. Pilot 50 first. The
$100-300 estimate was for 2,000 and needs redoing once the final count (3,000 + floor top-ups) is known.
Note: table 11 `State` is multi-value, so count a venue toward each state it lists, never with `==`. Decide the field list from this file before that run, so we pay for one pass, not several.
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

## 6. Small weddings and elopements (first real-user feedback, 2026-10-04)

A Forever couple (California + Hawaii, $10,000 budget, no guest count set) asked for an "elopement with only 6
people", then "somewhere beautiful", "cheaper options", "Big Sur?" and "any elopement packages in that area". They
rated the assistant 1/5: "It's repetitive and not the right stuff". They opened 0 PDFs. Repetition was fixed in
code (e77dfed). The "not the right stuff" half is mostly a data and search gap: our venues and fields are built
around 100+ guest weddings.

| Fact | Status today |
|---|---|
| Elopement / micro-wedding package offered (yes / no / unknown), its price, and max guests | NONE. Only appears if a PDF happens to name one (e.g. The Village, Big Sur: "$3,600 elopement" rental) |
| Guest minimum as a SEARCH filter ("minimum is at most N") | PARTIAL: the guest minimum is in the pricing summary the assistant reads, but search can only filter "seats at least N", so 50-guest-minimum venues still come back for a party of 6 |
| Food and drink minimum as a search ceiling | PARTIAL: `flt_min_fbmin` / `flt_max_fbmin` exist on table 11; whether ep119 can filter on them is not checked. $7,000-$10,000 minimums were recommended to a $10k couple |
| Ceremony-only or short-slot pricing (1-2 hours, no reception) | NONE |
| Weekday / off-peak small-party rates | PARTIAL: off-peak prices exist for some venues (venue_details), not for small parties |
| Small private dining room or restaurant buyout for under 20 | NONE (restaurants are in the data, room sizes aren't) |
| Officiant, photographer or florist bundled in the package | NONE |
| Public / scenic ceremony spots and permits (state parks, beaches, Big Sur overlooks) | NONE: these aren't venues with pricing PDFs; would need a separate source |

## Venues couples asked for by name that we don't have

Add a row each time the assistant has to say a venue isn't in Tulle's data. These are direct candidates for
outreach or PDF collection.

| Venue | Where | Asked | Notes |
|---|---|---|---|
| The Columns (hotel) | New Orleans, LA | 2026-10-04 | "Love the Columns hotel in New Orleans. Is this realistic?" Not in table 11 (checked by name). Forever user |

## Real-user question log (excluding Vivek, Des, Kate and test accounts)

As of 2026-10-04 (chats 21-29): 6 real users, all Forever buyers. 7 questions from 3 of them; the other 3
only saw the opening picks.

| Asked | Could we answer? | Gap |
|---|---|---|
| "Is The Columns hotel in New Orleans realistic?" | No | Venue not in our data (table above) |
| "yale club nyc" | Yes, well | None. Rental, per-head and fees all from the PDF |
| "Elopement with only 6 people" | Partly | Small-wedding gaps (section 6) |
| "Somewhere beautiful" | Partly | Style tags exist, but the picks still had 50-guest minimums |
| "Cheaper options" | Partly | No food-minimum ceiling in search |
| "Big Sur?" | Yes | Keyword search worked |
| "Any elopement packages in that area" | No | No elopement-package field (section 6) |

Also seen: when a couple's profile lists several states, the assistant searched only one (3 of 6 real users).
That was a code bug, not a data gap, fixed in e77dfed.

## How to decide what to build first

Measure demand before scraping. Count assistant questions (rec_messages, table 43) that mention each topic
once the assistant has a few weeks of real use, and optionally add a "traditions / needs" multi-select to
onboarding. Build the fields that couples actually ask about.

As of 2026-10-04 there are only 7 real-user questions (see the log above), so there is nothing to count yet. Once there are a few hundred real questions, add an "asked" count per row here (or move this list to a Xano
table if re-ranking by hand gets tedious). Choose which fields go into the planned top-3,000 + per-state floor venue PDF
re-extraction (pilot 50 first) from the top unanswered rows.
