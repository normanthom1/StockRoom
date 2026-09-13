"""Loading the shared catalogue (stock/catalogue_data.py) into the database,
and adding a catalogue product to a practice's own stock."""

from .catalogue_data import CATALOGUE, SUPPLIERS


def sync_catalogue(apps):
    """Make the catalogue tables match catalogue_data. Safe to run again: a
    later migration calls it after the data changes. Takes the migration's
    app registry, so it works on historical models."""
    Supplier = apps.get_model("stock", "CatalogueSupplier")
    Product = apps.get_model("stock", "CatalogueProduct")
    Offer = apps.get_model("stock", "CatalogueOffer")

    suppliers = {
        name: Supplier.objects.update_or_create(name=name, defaults={"website": website})[0]
        for name, website in SUPPLIERS.items()
    }
    kept, position = [], 0
    for category, (sellers, products) in CATALOGUE.items():
        for name, unit, extra in products:
            product, _ = Product.objects.update_or_create(
                name=name, defaults={"unit": unit, "category": category, "position": position}
            )
            position += 1
            Offer.objects.filter(product=product).delete()
            Offer.objects.bulk_create(
                Offer(product=product, supplier=suppliers[seller], position=i)
                for i, seller in enumerate(dict.fromkeys(sellers + extra))
            )
            kept.append(product.pk)
    Product.objects.exclude(pk__in=kept).delete()
    Supplier.objects.exclude(name__in=SUPPLIERS).delete()
