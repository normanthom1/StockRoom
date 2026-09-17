import csv
import io

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, ai_required
from accounts.ratelimit import AI_PER_HOUR, ai_limited
from stock import invoices
from stock.csv_import import REQUIRED_COLUMNS
from stock.models import InvoiceBatch, InvoiceBatchFile, Supplier
from stock.views import import_preview, invoice_preview

from . import gemini
from .snapshot import practice_snapshot

HISTORY_KEY = "ask_history"
HISTORY_TURNS = 6  # question-and-answer pairs sent back with each new question
MAX_QUESTION = 500
MAX_UPLOAD = 5 * 1024 * 1024
LIMITED = f"That's the limit for now ({AI_PER_HOUR} an hour each). Try again a bit later."

SUGGESTIONS = ["What's running low?", "How do I receive a delivery?"]
MANAGER_SUGGESTIONS = [
    "What should I order this week?",
    "What did we spend this month?",
    "What arrived this week?",
    "Anything still to come?",
]

CHAT_RULES = """You are Ask StockRoom, the help inside StockRoom, a stock-ordering app for a New Zealand dental practice.
Answer questions about how to use StockRoom (from the guide below) and about this practice's stock (from the data below).
- Plain New Zealand English. Short: a sentence or two, or a short list. Plain text only, no Markdown; start list lines with "- ".
- Only use the guide and the data. If the answer isn't there, say you don't know rather than guessing. Never make up numbers.
- You can't change anything in StockRoom; tell people where to tap to do it themselves.
- Stay on StockRoom and the practice's stock. Politely decline anything else, including clinical advice.
- You are talking to {name}, {role_description}.{price_rule}
"""
MANAGER_ROLE = "a manager, who can see prices and spending"
ASSISTANT_ROLE = "a dental assistant"
ASSISTANT_PRICE_RULE = " Assistants can't see prices or spending in StockRoom, and you don't have them; say it's one for the manager."


def _system_prompt(user):
    admin = user.is_org_admin
    rules = CHAT_RULES.format(
        name=user.name or "a staff member",
        role_description=MANAGER_ROLE if admin else ASSISTANT_ROLE,
        price_rule="" if admin else ASSISTANT_PRICE_RULE,
    )
    guide = render_to_string("assistant/guide.txt")
    return f"{rules}\n=== GUIDE ===\n{guide}\n=== PRACTICE DATA ===\n{practice_snapshot(user, timezone.localtime())}"


@ai_required
def ask(request):
    """Ask StockRoom: how-to and stock questions in one chat. The last few
    exchanges live in the session, which switching person replaces."""
    history = request.session.get(HISTORY_KEY, [])
    if request.method != "POST":
        suggestions = SUGGESTIONS + (MANAGER_SUGGESTIONS if request.user.is_org_admin else [])
        return render(request, "assistant/ask.html", {"history": history, "suggestions": suggestions})

    question = request.POST.get("q", "").strip()[:MAX_QUESTION]
    if not question:
        return HttpResponse(status=204)
    if ai_limited(request):
        answer = LIMITED
    else:
        turns = [turn for q, a in history for turn in (("user", q), ("model", a))]
        try:
            answer = gemini.generate(_system_prompt(request.user), [*turns, ("user", question)])
        except gemini.GeminiError:
            answer = "Sorry, I couldn't get an answer just now. Try again in a minute."
        else:
            # Gemini leans towards Markdown even when asked not to; bold is the usual leftover.
            answer = answer.replace("**", "")
            request.session[HISTORY_KEY] = [*history, [question, answer]][-HISTORY_TURNS:]
    return render(request, "assistant/ask.html#turn", {"q": question, "answer": answer})


IMPORT_RULES = """You turn a New Zealand dental practice's stock list, invoice or order sheet into rows for StockRoom's item import.
- One row per distinct product. Skip freight, GST, discounts, subtotals and totals.
- name: the product as staff would say it, with its size or variant, e.g. "Nitrile gloves, size M".
- unit: what one of it is counted in, singular and lowercase, e.g. "box", "cartridge", "pack".
- supplier: if it's one of this practice's suppliers, use that name exactly as written here: {suppliers}. Otherwise the supplier named on the document, or empty if there isn't one.
- price: the price for one unit in NZD, only if the document shows it.
- order_size: only if the document shows how many are ordered at a time (an invoice line's quantity counts).
- count: only if the document says how many are on the shelf now.
- Never invent a value; leave it out instead.
"""

ROW_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "unit": {"type": "STRING"},
            "supplier": {"type": "STRING"},
            "price": {"type": "NUMBER", "nullable": True},
            "order_size": {"type": "INTEGER", "nullable": True},
            "count": {"type": "INTEGER", "nullable": True},
        },
        "required": ["name", "unit", "supplier"],
    },
}


@require_POST
@ai_required
@admin_required
def import_with_ai(request):
    """A pasted list or a photo or PDF, read by Gemini into CSV rows, then the
    same checks and preview as a CSV upload. Nothing is saved until confirmed."""
    text = request.POST.get("text", "").strip()
    upload = request.FILES.get("document")
    attachment = None
    if upload:
        if upload.size > MAX_UPLOAD or not (upload.content_type or "").startswith(("image/", "application/pdf")):
            messages.error(request, "Choose a photo or a PDF under 5 MB.")
            return redirect("stock:item_import")
        attachment = (upload.content_type, upload.read())
    if not text and not attachment:
        messages.error(request, "Paste your stock list or choose a photo first.")
        return redirect("stock:item_import")
    if ai_limited(request):
        messages.error(request, LIMITED)
        return redirect("stock:item_import")

    suppliers = Supplier.objects.for_org(request.user.organisation).filter(is_active=True).order_by("name")
    rules = IMPORT_RULES.format(suppliers=", ".join(s.name for s in suppliers) or "(none set up yet)")
    try:
        rows = gemini.generate(rules, [("user", text or "Read the attached document.")], schema=ROW_SCHEMA,
                               attachment=attachment)
    except gemini.GeminiError:
        messages.error(request, "Couldn't read that just now. Try again, or import a CSV instead.")
        return redirect("stock:item_import")

    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if not rows:
        messages.error(request, "Couldn't find any items in that. Try a clearer photo, or paste the list as text.")
        return redirect("stock:item_import")
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REQUIRED_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: "" if row.get(column) is None else row[column] for column in REQUIRED_COLUMNS})
    return import_preview(request, out.getvalue())


@require_POST
@ai_required
@admin_required
def invoice_upload(request):
    """A PDF, photo or CSV read into a preview; nothing is saved until confirmed.
    A repeat of one already imported is saved as ignored and says so instead."""
    upload = request.FILES.get("invoice_file")
    if not upload:
        messages.error(request, "Choose an invoice first.")
        return redirect("stock:invoice_upload")
    mime_type = upload.content_type or ""
    is_csv = mime_type in invoices.CSV_TYPES
    if not _invoice_file_ok(upload):
        messages.error(request, "Choose a PDF, photo or CSV under 5 MB.")
        return redirect("stock:invoice_upload")
    if not is_csv and ai_limited(request):
        messages.error(request, LIMITED)
        return redirect("stock:invoice_upload")

    try:
        invoice, lines = invoices.parse_invoice(request.user.organisation, upload.read(), mime_type, upload.name)
    except gemini.GeminiError:
        messages.error(request, "Couldn't read that just now. Try again, or a CSV instead.")
        return redirect("stock:invoice_upload")
    if invoices.find_original(invoice):
        invoices.ingest(invoice, lines, request.user)
        return redirect("stock:invoice_detail", invoice.pk)
    return invoice_preview(request, invoice, lines)


MAX_BATCH = 50


def _invoice_file_ok(upload):
    mime_type = upload.content_type or ""
    return upload.size <= MAX_UPLOAD and (mime_type in invoices.CSV_TYPES or mime_type.startswith(("image/", "application/pdf")))


@require_POST
@ai_required
@admin_required
def invoice_batch_upload(request):
    """Several invoices at once, saved as a batch and imported in the background
    without a preview. Any that need checking are listed on the batch page."""
    uploads = request.FILES.getlist("invoice_files")
    if not uploads or len(uploads) > MAX_BATCH or not all(_invoice_file_ok(upload) for upload in uploads):
        messages.error(request, f"Choose up to {MAX_BATCH} PDFs, photos or CSVs, each under 5 MB.")
        return redirect("stock:invoice_upload")
    read_by_ai = sum(upload.content_type not in invoices.CSV_TYPES for upload in uploads)
    if read_by_ai and ai_limited(request, calls=read_by_ai):
        messages.error(request, LIMITED)
        return redirect("stock:invoice_upload")

    org = request.user.organisation
    with transaction.atomic():
        batch = InvoiceBatch.objects.create(organisation=org, created_by=request.user)
        InvoiceBatchFile.objects.bulk_create([
            InvoiceBatchFile(organisation=org, batch=batch, name=upload.name[:255], content_type=upload.content_type,
                             data=upload.read())
            for upload in uploads
        ])
        transaction.on_commit(lambda: invoices.start_batch(batch))
    return redirect("stock:invoice_batch", batch.pk)


@require_POST
@ai_required
@admin_required
def invoice_batch_resume(request, pk):
    """Start another run for a batch's files still waiting or that failed."""
    batch = get_object_or_404(InvoiceBatch.objects.for_org(request.user.organisation), pk=pk)
    left = batch.files.filter(state__in=invoices.TO_READ)
    read_by_ai = left.exclude(content_type__in=invoices.CSV_TYPES).count()
    if read_by_ai and ai_limited(request, calls=read_by_ai):
        messages.error(request, LIMITED)
    elif left.exists():
        invoices.start_batch(batch)
    return redirect("stock:invoice_batch", batch.pk)
