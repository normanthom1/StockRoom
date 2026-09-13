from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def record_order_suppliers(apps, schema_editor):
    """Every existing order went to its item's supplier; there was no other."""
    OrderLine = apps.get_model("stock", "OrderLine")
    Item = apps.get_model("stock", "Item")
    OrderLine.objects.filter(supplier__isnull=True).update(
        supplier_id=Subquery(Item.objects.filter(pk=OuterRef("item_id")).values("supplier_id")[:1])
    )


def load_catalogue(apps, schema_editor):
    from stock.catalogue import sync_catalogue

    sync_catalogue(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0006_catalogue_and_other_suppliers"),
    ]

    operations = [
        migrations.RunPython(record_order_suppliers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="orderline",
            name="supplier",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT, related_name="order_lines", to="stock.supplier"
            ),
        ),
        migrations.RunPython(load_catalogue, migrations.RunPython.noop),
    ]
