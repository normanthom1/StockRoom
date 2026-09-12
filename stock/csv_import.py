"""CSV import for onboarding a new practice's shelf.

Columns: name, unit, supplier, price, order_size, count. Only name, unit and
supplier are required; the rest are optional. Nothing is saved until the
preview is confirmed.
"""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

REQUIRED_COLUMNS = ["name", "unit", "supplier", "price", "order_size", "count"]


@dataclass
class ImportRow:
    line_number: int
    name: str = ""
    unit: str = ""
    supplier_name: str = ""
    price: Decimal | None = None
    order_size: int | None = None
    count: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self):
        return not self.errors


def parse_csv(csv_text: str, active_supplier_names: set[str]) -> tuple[list[ImportRow], list[str]]:
    """active_supplier_names must already be lowercased."""
    reader = csv.DictReader(io.StringIO(csv_text))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}."]

    rows = []
    for line_number, raw in enumerate(reader, start=2):  # the header is line 1
        row = ImportRow(line_number=line_number)
        row.name = (raw.get("name") or "").strip()
        row.unit = (raw.get("unit") or "").strip()
        row.supplier_name = (raw.get("supplier") or "").strip()
        if not row.name:
            row.errors.append("Name is required.")
        if not row.unit:
            row.errors.append("Unit is required.")
        if not row.supplier_name:
            row.errors.append("Supplier is required.")
        elif row.supplier_name.lower() not in active_supplier_names:
            row.errors.append(f'No supplier named "{row.supplier_name}".')

        price_raw = (raw.get("price") or "").strip()
        if price_raw:
            try:
                row.price = Decimal(price_raw)
                if row.price < 0:
                    raise InvalidOperation
            except InvalidOperation:
                row.errors.append("Price must be a number.")

        order_size_raw = (raw.get("order_size") or "").strip()
        if order_size_raw:
            try:
                row.order_size = int(order_size_raw)
                if row.order_size < 1:
                    raise ValueError
            except ValueError:
                row.errors.append("Order size must be a whole number of 1 or more.")

        count_raw = (raw.get("count") or "").strip()
        if count_raw:
            try:
                row.count = int(count_raw)
                if row.count < 0:
                    raise ValueError
            except ValueError:
                row.errors.append("Count must be a whole number of 0 or more.")

        rows.append(row)
    return rows, []
