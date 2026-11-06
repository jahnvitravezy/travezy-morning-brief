# Morning Brief: what the loader needs from the itinerary and preferences API

One page for Travezy's tech team. The loader (`mb/sources/itinerary.py`) reads the itinerary in
Travezy's existing format, plus the answers to the trip's planning questions. **Exists today** means
the field is in the itinerary format the service was built on; **New** means the tech team needs to
provide it. No names, emails or phone numbers are needed, and none are ever sent to a model.

## Requests

| Request | Status | Used for |
|---|---|---|
| `GET /trips?day_from=YYYY-MM-DD&day_to=YYYY-MM-DD` → trip IDs with a day in that range | New | Each batch run asks only for trips with a day in the coming window, never for every trip |
| `GET /trips/{trip_id}` → the itinerary and the trip's preferences | New (endpoint); the itinerary itself exists | One call per due trip |

## Itinerary (exists today)

| Field | Example | Used for |
|---|---|---|
| `plan[].dt` | `"2026-12-11"` | The trip day |
| `plan[].act_ttl` | `"Ubud Culture"` (`"Free day"` with no activities) | Free-day detection |
| `plan[].acts[].tm` | `"10:00 AM"` | Timeline, free windows, send time |
| `plan[].acts[].type` | `do`, `eat`, `move`, `relax_at_hotel_or_explore` | Which checks apply |
| `do`: `place`, `addr`, `hours`, `cost`, `desc`, `tips` | `"Tegallalang Rice Terrace"` | Places lookup, opening hours, access and weather checks |
| `eat`: `eat_name`, `eat_addr`, `cost`, `desc` | `"Arabian Tea House"` | Diet checks, meal and rest filter |
| `move`: `move_type`, `move_no`, `duration`, `cost`, `desc` | `"flight"`, `"QZ 7510"`, `"1 hour 30 minutes"` | Travel time, airport and transfer checks; the first flight's `desc` ("Flight Mumbai to Dubai.") gives the origin |
| `relax_at_hotel_or_explore`: `name`, `addr`, `desc` | `"Seminyak hotel"` | Where the traveller wakes up the next day |
| `place_identity`, `ignored_user_preference` | | Tracing; preferences the itinerary couldn't meet |

**Booked and timed entries:** there are no `booked` or `timed_at` fields today. The loader reads them
from `desc`: "booked" anywhere marks a booking, and "timed entry at 5:00 PM" or "pickup at 3:00 PM"
gives the fixed time. Structured fields would be more reliable (New, optional): `booked: true`,
`timed_at: "05:00 PM"`.

## Preferences (the trip's planning answers): New, as structured fields

| Field | Example | Used for |
|---|---|---|
| `who` | `"Couple in their 60s"` | Cautions for older travellers |
| `party.adults`, `party.children_ages` | `2`, `[6, 10]` | Family and safety checks |
| `diet.restrictions` | `["jain", "vegetarian"]` (also `vegan`, `no_egg`, `no_onion`, `no_garlic`) | Diet checks and food ideas |
| `diet.applies_to` | `"all"` or `"some"` | Wording ("for the vegetarians in your group") |
| `pace.start_not_before`, `pace.back_by` | `"09:00"`, `"20:00"` | Free windows, long-day checks |
| `pace.dinner_around` | `"19:00"` | Late-dinner check |
| `pace.max_walk_km` | `2` | Walking cautions; no walking ideas at 3 km or less |
| `pace.rest_after_lunch` | `true` | Keeps the rest break free |
| `pace.avoid_steep` | `true` | Steep-path checks |
| `budget` | `low`, `mid`, `mid_high`, `high` | Idea selection |
| `home_currency` | `"INR"` | Spend line in rupees |
| `interests` | `["temples", "cafes"]` | Idea searches |

## Errors

- Trip not found: 404. The service skips it and logs it.
- A missing optional field only skips the check it feeds.
- The loader waits 10 seconds (`config/limits.yaml`); a trip that times out is retried on the next run,
  15 minutes later.
