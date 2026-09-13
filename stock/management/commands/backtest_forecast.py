"""Replay each practice's history through the forecast and print how accurate
it would have been. See stock/backtest.py for the method and
docs/forecast-backtest.md for the baseline a new model has to beat.
"""

import djclick as click
from django.db.models import Max
from django.utils import timezone

from accounts.models import Organisation
from stock.backtest import HORIZON_WEEKS, backtest_item
from stock.models import Item, StockEvent


@click.command()
@click.option("--org", "org_name", help="Only this practice, by name. Default: every practice.")
def command(org_name):
    """Print a forecast accuracy table for each practice."""
    orgs = Organisation.objects.order_by("name")
    if org_name:
        orgs = orgs.filter(name=org_name)
        if not orgs:
            raise click.ClickException(f'No practice called "{org_name}".')
    for org in orgs:
        _report(org)


def _report(org):
    end = StockEvent.objects.for_org(org).aggregate(end=Max("created_at"))["end"]
    if end is None:
        click.echo(f"{org.name}: no stock history yet.\n")
        return
    items = (
        Item.objects.for_org(org).filter(is_active=True)
        .select_related("supplier").prefetch_related("events", "order_lines").order_by("name")
    )
    results = [
        (item, backtest_item(item.events.all(), item.order_lines.all(), lead_days=item.supplier.lead_days, end=end))
        for item in items
    ]

    click.echo(f"{org.name}: {HORIZON_WEEKS}-week forecasts, made weekly up to "
               f"{timezone.localtime(end):%d %b %Y}, against what was actually used.")
    click.echo(f"Used, MAE and bias are per {HORIZON_WEEKS} weeks, in each item's own unit. "
               "Bias above 0 means it over-predicted.\n")
    click.echo(f"{'Item':<34} {'Weeks':>5} {'Used':>7} {'MAE':>7} {'Bias':>7} {'Stock-outs':>10} {'Missed':>6}")
    unscored = []
    for item, r in results:
        if r.errors:
            scores = f"{sum(r.actuals) / len(r.actuals):>7.1f} {r.mae:>7.1f} {r.bias:>+7.1f}"
        elif r.stockouts:
            scores = f"{'-':>7} {'-':>7} {'-':>7}"
        else:
            unscored.append(item.name)
            continue
        click.echo(f"{item.name[:34]:<34} {len(r.errors):>5} {scores} {r.stockouts:>10} {r.missed:>6}")

    errors = [e for _, r in results for e in r.errors]
    used = sum(a for _, r in results for a in r.actuals)
    stockouts = sum(r.stockouts for _, r in results)
    missed = sum(r.missed for _, r in results)
    stockout_text = f"{stockouts} stock-out{'' if stockouts == 1 else 's'}, {missed} missed."
    if used:
        # MAE as a share of actual use (WAPE), so items with different units add up.
        click.echo(f"\nOverall: {len(errors)} forecasts, off by {sum(abs(e) for e in errors) / used:.0%} of actual use "
                   f"(bias {sum(errors) / used:+.0%}). {stockout_text}")
    else:
        click.echo(f"\nOverall: nothing to score yet. {stockout_text}")
    if unscored:
        click.echo(f"Not scored, no count {HORIZON_WEEKS}+ weeks after a count: {', '.join(unscored)}")
    click.echo("")
