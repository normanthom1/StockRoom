"""The numbers behind UX_issues.md, worked out from data StockRoom already
stores. Nothing here needs the analytics log: the log says when things happened,
this says where practices ended up.

The headline is setup completion rate - the share of practices that got from
signing up to a usable stock list. It is the one number UX-01 should move, and
before that fix a practice on an install with no AI key was sent to a 404 on the
first tap of the setup checklist.

    python manage.py ux_metrics
    python manage.py ux_metrics --days 30
"""

from datetime import timedelta

import djclick as click
from django.utils import timezone

from accounts.models import Organisation
from stock.models import Invoice, Item, ItemAlias, StockEvent


@click.command()
@click.option("--days", default=90, show_default=True, help="Only practices that signed up in the last N days.")
def command(days):
    """Print the UX success metric and the counts behind it."""
    since = timezone.now() - timedelta(days=days)
    practices = Organisation.objects.filter(created_at__gte=since)
    total = practices.count()

    click.echo(f"Practices that signed up in the last {days} days: {total}")
    if not total:
        click.echo("Nothing to measure yet.")
        return

    # The success metric: did they end up with a stock list they can use?
    with_stock = sum(1 for org in practices if Item.objects.for_org(org).filter(is_active=True).exists())
    counted = sum(1 for org in practices if StockEvent.objects.for_org(org).filter(kind="count").exists())

    click.echo("")
    click.secho(f"  Setup completion rate: {_pct(with_stock, total)}", bold=True)
    click.echo(f"    {with_stock} of {total} reached a stock list with at least one active item")
    click.echo(f"    {counted} of {total} also counted the shelf ({_pct(counted, total)})")

    # The backlog UX-03 made findable: invoices that received nothing.
    needs_checking = Invoice.objects.filter(organisation__in=practices, status=Invoice.Status.CONFLICT).count()
    imported = Invoice.objects.filter(organisation__in=practices).exclude(status=Invoice.Status.IGNORED).count()
    click.echo("")
    click.echo(f"  Invoices imported: {imported}")
    click.echo(f"    needing checking, so nothing received off them: {needs_checking}"
               f" ({_pct(needs_checking, imported)})")

    # Whether the review flows UX-02 and the merges page exist for a reason.
    merges = ItemAlias.objects.filter(organisation__in=practices).count()
    undone = ItemAlias.objects.filter(organisation__in=practices, reverted_at__isnull=False).count()
    click.echo("")
    click.echo(f"  Names matched to an existing item: {merges}")
    click.echo(f"    undone by a manager as wrong: {undone} ({_pct(undone, merges)})")


def _pct(part, whole):
    return f"{round(100 * part / whole)}%" if whole else "n/a"
