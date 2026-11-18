---
name: project_image_delivery_cost
description: "Vendor images are the dominant page-load cost: 800px ~200KB JPEGs behind an UNCACHEABLE Xano vault 303. Measured 2026-09-13."
metadata:
  type: project
---

Measured 2026-09-13 from table 11 (40 Venue rows) and live curl. Answers "why do pictures load
after the page" and most of "why is switching categories slow".

## The image chain, and the trap in it

`image_1/2/3` are Xano **vault** file objects. Loading one is TWO hops:

1. `https://xqtb-2ma7-ijfy.n7e.xano.io/vault/.../vendor_<id>.jpg` -> **303**, ~200ms, and its
   headers are `Cache-Control: private, no-cache, no-store, must-revalidate`.
2. -> `https://storage.googleapis.com/xqtb-2ma7-ijfy.n7e.xano.io/vault/.../vendor_<id>.jpg`,
   which IS cacheable (`public, max-age=3600`).

**The bytes can be cached; the redirect in front of them cannot.** So every image costs a fresh
~200ms round-trip to Xano on every page view, forever. The GCS URL is stable and public, so
pointing at it directly (or fronting it with Cloudflare) removes that hop outright.

## Sizes — this is the real weight

- `image_1`: mean **215KB**, median 169KB, **max 728KB**. Width is **always 800px** (29/31 rows).
  There is no thumbnail and no resize step.
- Vendors either have all three images or none: **31 of 40 had 3, 9 had 0** — that zero group is
  the standing [[project_frontend_images_issue]] "blank card" symptom, and it is missing DATA,
  not a rendering bug.
- **A 12-card Discovery grid = ~2.5MB of hero images + 12 uncacheable redirects.**
- A vendor page = ~0.57MB across 3 images.

800px/200KB is being shipped into a card slot roughly 300px wide. Resizing to ~400px WebP is
the single largest available win and it is a pipeline change (`tulle_vendor_data_updates`),
not a Xano or WeWeb one.

## ep41 ships the whole row, and 75% of it is Google junk

`ep41 wptp_updated_mappings/{id}` ends in `response = $model` with **no `output` list**, so the
vendor page receives the entire row. `google_data_cache` is **75% of it** (mean 8.4KB of an
11.2KB row): **36% Google review TEXT, 38% `photo_reference` strings**. The page renders its
images from `image_1/2/3`, so none of that is used.
**But ep41 measures 0.07-0.42s — it is NOT the bottleneck.** Trimming it is a cleanup worth ~8KB
a page, not a fix. Do not sell it as one.

Contrast: **ep119 already trims correctly** (explicit `output` list, `google_data_cache` count
zero) — which is why [[project_flt_states_denorm]]'s "payload trimming is a near-zero win for
ep119" stands.

## What actually dominates each complaint

- **Category switch**: ep119 cache miss. Hit p50 **68ms** vs miss p50 **1,344ms** / p95 4,297ms /
  max 8,525ms — a 20x cliff. See [[project_ep119_category_cache_gap]].
- **Vendor page "pictures come in late"**: the page itself is fine (ep41 sub-0.5s); it is the
  3 images x (uncacheable 303 + ~191KB). Also ep119 fires on the vendor page for Similar Vendors
  and was observed at **4.74s uncached, landing t+5s** — that is why that strip pops in last.

**UNVERIFIED:** that the browser requests the raw vault URL rather than a WeWeb image proxy. The
chain above is inferred from the row data plus ep119's `output` list shipping `image_1` verbatim.
One DevTools Network check (filter: Img) settles it — do that before acting on items 1-2.

## 2026-09-13 (same day, later) — WHERE 800px COMES FROM, AND WHY 400px WOULD BE WRONG

**The source of the 800px is a literal**: `maxwidth: 800` in the Google Places Photo call inside
**ep132 / ep133 / ep134** (`update_vendor_image_one/two/three`, group 10, secret-gated). Changing
it is one number in three endpoints.

**But do not just lower it.** Measured display boxes in WeWeb:

| slot | rendered size | element |
|---|---|---|
| Discovery card | `width:100%` x **`height:180px`**, `object-fit:cover` (~300x180 CSS) | `903104e9-…` "Vendor Image 1" |
| Vendor page hero, desktop | grid `2fr 1fr`, `max-height:500px`, spans both rows (~700px wide) | `3d62a28a-…` "Main Gallery Image" |
| Vendor page images 2/3, desktop | the `1fr` column (~350px wide) | `b3265461-…`, `d700377e-…` |
| Vendor page, mobile | snap carousel, `flex:0 0 88%`, `aspect-ratio:16/10` (~310 CSS px) | same three |

At DPR 2-3 the phone carousel wants **620-930 device px** and the desktop hero ~700. **800px is
about right for the largest slot** — dropping to 400 would visibly soften the vendor-page hero and
every image on a retina phone. The waste is not pixel COUNT:

1. **Format/quality.** 215KB for 800x534 is ~0.5 bytes/px, a high-quality JPEG. WebP q75 is
   ~60-70KB for the same pixels — **3x smaller, zero resolution lost.** Google's photo API returns
   JPEG only with no quality knob, so this needs a re-encode or a CDN.
2. **Height.** `object-fit:cover` into a 5:3 card box throws away ~55% of an 800x1200 portrait
   AFTER downloading it. The photo API also accepts **`maxheight`** — setting both would kill the
   728KB outliers without touching width.
3. **One master for every slot**, so gallery images 2/3 download an 800px file into a 350px box.

So the correct single move is an **image CDN in front of one 800px master** (format negotiation +
on-the-fly resize + `srcset`). That collapses this item and the redirect item into one job and
needs **no backfill and no re-fetch from Google**. Lowering `maxwidth` does not.

**Backfilling is the expensive half either way:** ep132's `where` is `image_1 == null`, so the
batch SKIPS rows that already have an image — a re-run needs that condition changed AND re-calls
the **billed** Places Photo API per image, and `storage.create_image` writes NEW vault files while
the old ones linger.

**CORRECTION to the earlier entry: `loading: "lazy"` is ALREADY set** on both the Discovery card
image and all three gallery images. So "12 x 215KB = 2.5MB" is the **full-scroll** figure, not the
initial load; browsers pre-load lazily within a generous margin, so the real initial cost on a
desktop grid is somewhere between ~6 and 12 images. Adding lazy-loading is not an available win —
it is done.
