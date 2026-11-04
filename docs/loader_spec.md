# Morning Brief: what the loader needs from the itinerary and preferences API

One page for Travezy's tech team. The loader (`mb/sources/itinerary.py`) asks for one trip by its ID
and needs the fields below. Everything else in the itinerary is ignored. **No names, emails or
phone numbers**: the service doesn't need them and never sends them to a model.

## Request

`GET <itinerary API>/trips/{trip_id}`, called once per trip day, a few hours before the send time.

## Trip

| Field | Type | Required | Example | Used for |
|---|---|---|---|---|
| `trip_id` | string | yes | `"t_8f2c"` | Outbox key (one brief per trip per day) |
| `itinerary_id` | string | yes | `"it_5521"` | Tracing |
| `destination` / `region` | string | yes | `"Bali"` | Weather, searches, title |
| `origin` | string | no | `"Mumbai"` | Arrival-day time zone |
| `plan` | list of days | yes | below | Everything |
| `preferences` | object | yes | below | Diet, pace, ideas |

## Day (`plan[]`)

| Field | Type | Required | Example |
|---|---|---|---|
| `dt` | date, `YYYY-MM-DD` | yes | `"2026-12-11"` |
| `act_ttl` | string | yes | `"Ubud Culture"` (`"Free day"` with no activities) |
| `acts` | list of activities | yes (may be empty) | below |

## Activity (`acts[]`)

| Field | Type | Required | Example | Used for |
|---|---|---|---|---|
| `tm` | local time, `hh:mm AM/PM` | yes | `"10:00 AM"` | Timeline, free windows, send time |
| `type` | `do`, `eat`, `move`, `stay` | yes | `"do"` | Which checks apply |
| `place` | string | for do, eat, stay | `"Tegallalang Rice Terrace"` | Places lookup, title |
| `addr` | string | recommended | `"Tegallalang, Gianyar, Bali"` | Finding the right place |
| `cost` | text with currency | no | `"IDR 25,000 per person"` | Spend line |
| `desc` | string | no | `"Outdoor, uneven paths."` | Access and weather checks |
| `hours` | string | no | `"10:00 AM - 12:00 PM"` | Slot length |
| `duration` | string | for move | `"1 hour 30 minutes"` | Travel time |
| `move_type`, `move_no` | string | for move | `"flight"`, `"QZ 7510"` | Airport and transfer checks |
| `booked` | boolean | recommended | `true` | Heads-ups for timed bookings |
| `timed_at` | local time | if a booking has a fixed time | `"04:30 PM"` | "Be there by" lines |

## Preferences (answers to the trip's planning questions)

| Field | Type | Example | Used for |
|---|---|---|---|
| `party.adults`, `party.children_ages` | int, list of int | `2`, `[6, 10]` | Family checks |
| `diet.restrictions` | list | `["vegetarian", "no_egg"]` (also `jain`, `vegan`, `no_onion`, `no_garlic`) | Diet checks and food ideas |
| `diet.applies_to` | `all` or `some` | `"some"` | Wording ("for the vegetarians in your group") |
| `pace.start_not_before`, `pace.back_by` | local time | `"09:00"`, `"20:00"` | Free windows |
| `pace.rest_after_lunch` | boolean | `true` | Keeps the rest break free |
| `pace.avoid_steep` | boolean | `true` | Access checks |
| `budget` | `low`, `mid`, `mid_high`, `high` | `"mid"` | Idea selection |
| `home_currency` | ISO code | `"INR"` | Spend line in rupees |
| `interests` | list | `["temples", "cafes"]` | Idea searches |

## Errors

- Trip not found: 404. The service skips it and logs it.
- A day with no activities is a free day. A missing optional field just skips the check it feeds.
- The API should answer in under 2 seconds; the loader waits 10 seconds, then the brief is skipped
  and retried on the next batch run (every 15 minutes).
