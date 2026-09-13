from django import template

from stock.humanize import pluralize_unit

register = template.Library()


@register.filter
def plural(unit, qty=2):
    """{{ item.unit|plural }} -> "boxes"; {{ item.unit|plural:order.qty }} for an actual count."""
    return pluralize_unit(unit, qty)
