from django.core.management.base import BaseCommand

from stock.invoices import run_batch
from stock.models import InvoiceBatch


class Command(BaseCommand):
    help = "Read and import a batch of uploaded invoices. Safe to run again: only files waiting or that failed are read."

    def add_arguments(self, parser):
        parser.add_argument("batch_id", type=int)

    def handle(self, *args, batch_id, **options):
        run_batch(InvoiceBatch.objects.get(pk=batch_id))
