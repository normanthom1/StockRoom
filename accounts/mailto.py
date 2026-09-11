"""Building mailto: links, so StockRoom never needs to send email itself.

An admin clicks one of these to open their own mail client (Gmail, Outlook,
whatever they're logged into), addressed and pre-filled, and sends it from
their own account. See #13 for why: no provider, no SMTP, no API key.
"""

from urllib.parse import quote


def build_mailto_link(to, subject, body):
    # @ is valid and expected unencoded in a mailto address (RFC 6068); quote()
    # would otherwise turn it into %40, which some mail clients mishandle.
    return f"mailto:{quote(to, safe='@')}?subject={quote(subject)}&body={quote(body)}"
