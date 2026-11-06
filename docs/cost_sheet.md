# Morning Brief: cost sheet (revised 6 Nov 2026)

**Basis**
- Google's India pricing ([pricing-india](https://developers.google.com/maps/billing-and-pricing/pricing-india)), checked 3 Nov.
- At ₹95.22 per USD, checked 30 Oct.
- Call counts measured on the 22 test days.

## Per brief

| | Before (US pricing, no table) | Now (India pricing, places table, shared cache) |
|---|---|---|
| Models (GPT-4o writing, gpt-4o-mini judging; baseline-v9) | ₹1.22 | ₹1.32 |
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

## Per month (3.3), from `python -m mb.evals.cost_report baseline-v9`

| | 50 travellers a day (1,500 briefs) | 100 travellers a day (3,000 briefs) |
|---|---|---|
| Models (GPT-4o, measured on baseline-v9) | ₹1,980 | ₹3,960 |
| Google calls with the places table warm | within the free allowance: ₹0 | within the free allowance: ₹0 |
| Web search (Tavily) | about ₹80 | about ₹165 |
| **Total, with the free allowance** | **about ₹2,060 (₹1.37 a brief)** | **about ₹4,120 (₹1.37 a brief)** |
| **Worst case: free allowance already used by your other apps** | **about ₹3,175 (₹2.12 a brief)** | **about ₹6,350 (₹2.12 a brief)** |

**Worst case (7):** Google's free allowance is per billing account, and Travezy's app already uses
Places. If that usage has already taken the free calls, every Place Details call ($6 per 1,000) and
idea search ($10.50 per 1,000) is paid: about ₹0.74 a brief more. Even then, data stays far below the
old ₹12 a brief.

Models are now most of the cost, so the model choice (1.1) is the lever: with gpt-5-mini as Summariser
or Composer, models fall from ₹1.32 to about ₹0.90 a brief.
