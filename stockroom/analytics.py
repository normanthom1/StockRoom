"""Structured events for the flows a UX change is meant to move.

StockRoom had no instrumentation at all, so nobody could answer "do new
practices get a stock list?" or "does anyone ever undo an import?", and a UX
change shipped on argument alone.

Deliberately not a third-party analytics script. StockRoom self-hosts
everything, runs a strict CSP with no inline script, and tells practices it
keeps "nothing about patients" - a tag that phones out to someone else's server
would break all three. This is Python's own logging, which Django already
configures and Railway already collects, written as one JSON object per line so
the lines can be grepped or fed to anything later.

No personal data: an organisation id, and counts. Never a name, an email, an
item, a price or a staff code.

ponytail: log lines, not rows in a table. If a question ever needs a join or a
time series, put a real events table behind track() - the call sites won't change.
"""

import json
import logging

logger = logging.getLogger("stockroom.analytics")

# The flows worth watching, so a typo in a call site is obvious rather than
# quietly creating a seventh event nobody reads.
EVENTS = {
    "setup_step_opened",  # a manager opened the setup checklist
    "setup_completed",  # every step done, or put away
    "invoice_imported",  # an invoice was ingested, with how it landed
    "invoice_needs_checking",  # it landed as a conflict: nothing received off it
    "invoice_undone",  # an import was undone inside the window
    "merge_undone",  # a matched name turned out not to be that item
}


def track(event, *, org=None, **fields):
    """Record that something happened.

    An unknown event is a typo in a call site, which is a programming error and
    raises so a test catches it. Anything going wrong while writing the line is
    swallowed: measurement must never break the thing being measured.
    """
    if event not in EVENTS:
        raise ValueError(f"Unknown analytics event {event!r}. Add it to EVENTS.")
    try:
        logger.info(json.dumps({"event": event, "org": org, **fields}, default=str, sort_keys=True))
    except Exception:
        logger.exception("Couldn't record analytics event %r", event)
