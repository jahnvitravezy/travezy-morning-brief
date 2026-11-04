# Morning Brief: cost sheet (revised 4 Nov 2026)

**Basis**
- Google's India pricing ([pricing-india](https://developers.google.com/maps/billing-and-pricing/pricing-india)), checked 3 Nov.
- At ₹95.22 per USD, checked 30 Oct.
- Call counts measured on the 22 test days.

## Per brief

| | Before (US pricing, no table) | Now (India pricing, places table, shared cache) |
|---|---|---|
| Models (GPT-4o writing, gpt-4o-mini judging) | ₹1.22 | ₹1.22 |
| Models with GPT-4.1 mini as Surprise finder (3.4) | | **₹0.82** ($0.0086, score 87.7 vs 88.3, a tie) |
| Models with its replacement, gpt-5-mini, as Surprise finder | | **₹1.20** ($0.0126, first score 84.5 vs 82.4) |
| Models with gpt-5-mini as Summariser or Composer (minimal reasoning) | | **₹0.88–0.90** ($0.0092–0.0095) |
| Data, nothing stored yet (first traveller to a place) | ₹16.85 | **₹4.0** |
| Data, places table and cache warm (80% of lookups stored) | ₹12 | **₹0.80** before the free allowance |

**Why data fell (3.2):**
- A planned stop is now found once with an IDs-only search, which is free. Only its details are paid: Place Details, $6 per 1,000 calls instead of $35.
- After that, the stop is served from the places table, with hours refreshed every 3 days.
- "Ideas near here" use one Text Search for 5 places ($10.50 per 1,000 calls), and every place returned is stored.

**Measured per brief, from nothing:** 4.9 Google place calls (about 2.9 stop lookups and 2 idea searches), 0.45 route elements, 0.36 web searches.

## Per month, with the free allowance deducted (3.3)

| | 50 travellers a day (1,500 briefs) | 100 travellers a day (3,000 briefs) |
|---|---|---|
| Place Details calls (table warm) | 870 (free up to 7,000) | 1,740 (free up to 7,000) |
| Idea searches (table warm) | 600 (free up to 7,000) | 1,200 (free up to 7,000) |
| Route elements | 135 (free up to 70,000) | 270 (free up to 70,000) |
| **Google, total** | **₹0** | **₹0** |
| Web search (Tavily) | about ₹80 | about ₹165 |
| Models, GPT-4o | ₹1,830 | ₹3,660 |
| **Total, GPT-4o** | **about ₹1,910 (₹1.27 a brief)** | **about ₹3,825 (₹1.28 a brief)** |
| **Total, GPT-4.1 mini as Surprise finder** | **about ₹1,310 (₹0.87 a brief)** | **about ₹2,625 (₹0.88 a brief)** |

Even with nothing stored, 100 a day stays close to the free allowance: 8,700 Place Details calls,
which is 1,700 over the free 7,000, about ₹970 a month. Models are now most of the cost, so the
model choice (1.1) is the lever. GPT-4o and GPT-4.1 mini retire in January 2027. The replacement
models' scores and costs are in the reply and `docs/build_log.md`.
