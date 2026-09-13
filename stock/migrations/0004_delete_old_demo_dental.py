"""Remove the old "Demo Dental" demo practice and its accounts.

The demo moved to "Discover Dental" with a practice login and staff codes, so
nothing resets "Demo Dental" any more, and its sandy@demodental.test-style
logins (with a published password) would otherwise live on. Deleted in
dependency order: events and orders protect their user, items protect their
supplier, and users protect the organisation.

The demo's reset marker is cleared too, so a DEMO_MODE deployment seeds
Discover Dental on its next request instead of waiting for the next night.
"""

from django.db import migrations

OLD_DEMO_ORG = "Demo Dental"


def delete_old_demo(apps, schema_editor):
    Organisation = apps.get_model("accounts", "Organisation")
    User = apps.get_model("accounts", "User")
    for name in ("StockEvent", "OrderLine", "Item", "Supplier"):
        apps.get_model("stock", name).objects.filter(organisation__name=OLD_DEMO_ORG).delete()
    User.objects.filter(organisation__name=OLD_DEMO_ORG).delete()
    Organisation.objects.filter(name=OLD_DEMO_ORG).delete()
    apps.get_model("stock", "DemoResetState").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0003_demoresetstate"),
        ("accounts", "0003_practice_login_and_staff_codes"),
    ]

    operations = [migrations.RunPython(delete_old_demo, migrations.RunPython.noop)]
