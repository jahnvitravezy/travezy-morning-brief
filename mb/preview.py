"""Web preview: how a brief looks to the traveller, as a phone-sized card.

  GET /preview/{date}/{trip_id}                       a brief from the outbox, in the browser
  python -m mb.preview data/runs/judged-v2/briefs/td.02.json   any saved brief -> .html beside it

For review and demos only. The real app renders the JSON itself; this shows what it contains.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

from mb.contracts import MorningBrief

BADGE = {"green": ("#1e7b3c", "Checked"), "amber": ("#a15c00", "Checked, with notes"), "red": ("#b42318", "Plan only")}
ACTION = {"add_to_day": "Add to my day", "swap": "Swap", "ask": "Ask a question"}


def _e(x) -> str:
    return html.escape(str(x)) if x not in (None, "") else ""


def render(b: MorningBrief) -> str:
    badge = BADGE.get(b.confidence.badge or "", ("#5b6475", "Not checked"))
    timeline = "".join(f"<li><span class=t>{_e(t.time)}</span> {_e(t.name)}"
                       f"{' <span class=bk>booked</span>' if t.booked else ''}"
                       f"{f'<div class=tr>{t.travel_to_next_min} min to next</div>' if t.travel_to_next_min else ''}</li>"
                       for t in b.timeline) or "<li>Free day: nothing planned</li>"
    heads = "".join(f"<li>{_e(h)}</li>" for h in b.heads_ups) or "<li>Nothing to flag today</li>"
    ideas = "".join(f"""<div class=idea><div class=in>{_e(s.name)}</div>
        <div class=im>{_e(s.fits_slot)}{f' · {s.distance_min} min away' if s.distance_min else ''}</div>
        <div>{_e(s.why_you)}</div>{f'<a href="{_e(s.source_url)}" target=_blank>Map</a>' if s.source_url else ''}</div>"""
                    for s in b.surprises) or "<p class=mut>No extra ideas today.</p>"
    actions = "".join(f"<button>{ACTION[a]}</button>" for a in b.actions)
    w = b.weather
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{_e(b.notification.title)}</title><style>
body{{margin:0;background:#e9ecf2;font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:#1d2433}}
.fine{{font-size:11px;color:#7a8394;margin-top:4px}} .fine a{{color:inherit}}
.phone{{max-width:390px;margin:24px auto;background:#fff;border-radius:28px;box-shadow:0 8px 30px #0002;overflow:hidden}}
.lock{{background:linear-gradient(160deg,#1F3864,#3b5ba5);color:#fff;padding:18px 16px 22px}}
.time{{font-size:12px;opacity:.8}} .notif{{background:#ffffff22;border-radius:14px;padding:10px 12px;margin-top:10px}}
.nt{{font-weight:700}} .sec{{padding:12px 16px;border-top:1px solid #eef0f4}} h3{{margin:0 0 6px;font-size:13px;
text-transform:uppercase;letter-spacing:.04em;color:#5b6475}} ul{{margin:0;padding-left:18px}} li{{margin:3px 0}}
.t{{font-weight:700;display:inline-block;min-width:44px}} .bk{{font-size:11px;background:#e8f0ff;color:#1F3864;border-radius:6px;padding:1px 5px}}
.tr{{font-size:12px;color:#5b6475;margin-left:44px}} .badge{{display:inline-block;color:#fff;border-radius:10px;padding:2px 8px;font-size:12px}}
.idea{{border:1px solid #e3e7ef;border-radius:12px;padding:8px 10px;margin:6px 0}} .in{{font-weight:700}} .im{{font-size:12px;color:#5b6475}}
.mut{{color:#5b6475;margin:0}} .clash{{color:#a15c00}} button{{border:1px solid #1F3864;background:#fff;color:#1F3864;border-radius:18px;
padding:6px 12px;margin:4px 4px 0 0;font:inherit;font-size:13px}} a{{color:#1F3864;font-size:13px}}
</style></head><body><div class=phone>
<div class=lock><div class=time>Sent {_e(b.send_at_local)} {_e(b.timezone)}{f' · {_e(b.send_at_utc)}' if b.send_at_utc else ''}</div>
<div class=notif><div class=nt>{_e(b.notification.title)}</div><div>{_e(b.notification.body)}</div></div></div>
<div class=sec><span class=badge style="background:{badge[0]}">{badge[1]}{f' · {b.confidence.score}' if b.confidence.score is not None else ''}</span></div>
<div class=sec><h3>Today</h3><ul>{timeline}</ul></div>
<div class=sec><h3>Weather</h3><div>{_e(w.summary)}</div>{f'<div class=clash>{_e(w.clash)}</div>' if w.clash else ''}
{f'<div>Swap: {_e(w.suggested_swap)}</div>' if w.suggested_swap else ''}</div>
<div class=sec><h3>Heads-ups</h3><ul>{heads}</ul></div>
<div class=sec><h3>Ideas for you</h3>{ideas}</div>
<div class=sec><h3>Spend today</h3><div>{_e(b.spend_estimate.local)}{f' ({_e(b.spend_estimate.home)})' if b.spend_estimate.home else ''}</div>{'<div class=fine><a href="https://www.exchangerate-api.com">Rates By Exchange Rate API</a></div>' if b.spend_estimate.home else ''}</div>
<div class=sec><h3>Local tip</h3><div>{_e(b.local_tip)}</div></div>
<div class=sec>{actions}</div></div></body></html>"""


if __name__ == "__main__":
    for p in map(Path, sys.argv[1:]):
        out = p.with_suffix(".html")
        out.write_text(render(MorningBrief.model_validate_json(p.read_text(encoding="utf-8"))), encoding="utf-8")
        print(out)
