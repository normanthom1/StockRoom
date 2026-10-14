import csv
import io
import time
import uuid

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import resolve, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, ai_required
from accounts.ratelimit import AI_PER_HOUR, ai_limit_is_personal, ai_limited
from stock import invoices
from stock.csv_import import REQUIRED_COLUMNS
from stock.models import Invoice, InvoiceBatch, InvoiceBatchFile, Supplier
from stock.views import UNDO_WINDOW, fill_invoice, import_preview, invoice_preview

from . import actions, gemini
from .snapshot import practice_snapshot

HISTORY_KEY = "ask_history"  # shown on Home through context_processors.ask_turns
UNDO_KEY = "ask_undo"  # a deleted chat, kept until the next question
HISTORY_TURNS = 6  # question-and-answer pairs sent back with each new question
HISTORY_MAX_AGE = 24 * 60 * 60  # seconds a turn is kept
# The chat's change cards, {id: proposal}: waiting for Confirm until CHANGE_AGE
# is up, then once confirmed, kept until the toast's Undo has run out.
CHANGES_KEY = "ask_changes"
LINKS_KEY = "ask_links"  # {str(turn time): buttons} for answers with buttons under them
CHANGE_AGE = 10 * 60
CLEAR_UNDO = '<div id="chat-undo" hx-swap-oob="true"></div>'
MAX_QUESTION = 500
MAX_UPLOAD = 5 * 1024 * 1024
LIMITED = f"That's the limit for now ({AI_PER_HOUR} an hour each). Try again a bit later."

# Where the reason for a failed invoice read is left for the upload page to show.
# A toast is gone in six seconds and the page looks untouched, so a manager who
# glanced away can't tell whether anything happened - and uploads the same
# failing file again, spending another AI call on it.
UPLOAD_ERROR_KEY = "invoice_upload_error"
READ_FAILED = ("StockRoom couldn't read this one. A clearer photo usually does it: flat, in good light, with "
               "the whole invoice in frame. A PDF or CSV from the supplier works better still, and is read without AI.")


def _upload_failed(request, filename, text):
    """Leave the reason on the upload page itself, not only in a toast."""
    request.session[UPLOAD_ERROR_KEY] = {"file": filename, "text": text}
    return redirect("stock:invoice_upload")


def _limit_text(request):
    """Why the read was refused and when it will work again. Which limit was hit
    decides whether "later" means an hour or tomorrow."""
    if ai_limit_is_personal(request):
        return (f"That's {AI_PER_HOUR} files read in the last hour, which is the limit. "
                "Try again in an hour, or upload a CSV instead - those are read without AI, "
                "so they always work.")
    return ("StockRoom has read as many files as it can today, across every practice. "
            "Try again tomorrow, or upload a CSV instead - those are read without AI, "
            "so they always work.")

MONEY_REFUSAL = actions.MONEY_REFUSAL

CHAT_RULES = """You are Ask StockRoom, the help inside StockRoom, a stock-ordering app for a New Zealand dental practice.
Answer questions about how to use StockRoom (from the guide below) and about this practice's stock (from the data below).
- Plain New Zealand English. Short: a sentence or two, or a short list. Plain text only, no Markdown; start list lines with "- ".
- Only use the guide and the data. If the answer isn't there, say you don't know rather than guessing. Never make up numbers.
- You can make some changes with your functions, such as logging what's been used or counted. Call the function and StockRoom shows them a card with the change; nothing happens until they tap Confirm. If one message has several changes, call a function for each. Never say you've changed something yourself: the conversation says what was done, cancelled or undone. For anything your functions can't do, tell them where to tap to do it themselves.
- Where the best answer is to take them to a page, or to hand them a CSV export, call open_page or download as well as answering in words from the data. The button goes under your answer, and nothing changes when it's tapped.
- Stay on StockRoom and the practice's stock. Politely decline anything else, including clinical advice.
- You are talking to {name}, {role_description}.{role_rule}
"""
MANAGER_ROLE = "a manager, who can see prices and spending"
MANAGER_RULE = """
- Managers may ask where to save money. Answer from the data: the items with the biggest spend, prices that went up between invoices, items with a backup supplier worth asking for a quote, and anything ordered well ahead of need. Name the items and the dollar figures, and say these are ideas to check, not promises.
- Managers may ask "what if" questions with a price they give you, e.g. "what would I save a year if gloves were $7.20 a box?". Use their price, and the item's yearly usage and yearly cost from the data. Show the sum in one line (e.g. 520 boxes x $1.30 less = $676 a year), and say it assumes usage stays the same. If they don't say which item or give no price, ask for it. You don't know other suppliers' or brands' prices; never guess one."""
ASSISTANT_ROLE = "a dental assistant"
ASSISTANT_RULE = (
    "\n- Assistants can't see prices, costs or spending in StockRoom, and you don't have them. If they ask about "
    "prices, costs, spending, invoices, budgets or saving money, reply with exactly this and nothing else: "
    f'"{MONEY_REFUSAL}"'
)


def _system_prompt(user):
    admin = user.is_org_admin
    rules = CHAT_RULES.format(
        name=user.name or "a staff member",
        role_description=MANAGER_ROLE if admin else ASSISTANT_ROLE,
        role_rule=MANAGER_RULE if admin else ASSISTANT_RULE,
    )
    guide = render_to_string("assistant/guide.txt")
    return f"{rules}\n=== GUIDE ===\n{guide}\n=== PRACTICE DATA ===\n{practice_snapshot(user, timezone.localtime())}"


def recent_turns(session):
    """The conversation still worth showing: [question, answer, when] from the last day.
    Older ones (and any saved before turns were dated) are dropped, so a chat from
    yesterday's stock doesn't linger on Home."""
    cutoff = time.time() - HISTORY_MAX_AGE
    return [turn for turn in session.get(HISTORY_KEY, []) if len(turn) == 3 and turn[2] > cutoff]


def chat_turns(session):
    """(question, answer, change, buttons) for each turn on show: change is its
    card, while that's still waiting for Confirm or can still be undone, and
    buttons are the pages or files its answer pointed to."""
    cards = {change["turn"]: change for change in _changes(session).values()}
    links = session.get(LINKS_KEY, {})
    return [(q, a, cards.get(when), links.get(str(when))) for q, a, when in recent_turns(session)]


def _changes(session):
    now = time.time()
    return {pid: change for pid, change in session.get(CHANGES_KEY, {}).items() if change["expires"] > now}


def _record(session, change, what):
    """Rewrite the chat's own record of a change: shown once its card has gone,
    and sent with the next question so the AI knows what happened."""
    text = f"{what}: {change['summary']}."
    session[HISTORY_KEY] = [[q, text if when == change["turn"] else a, when] for q, a, when in recent_turns(session)]


@ai_required
def ask(request):
    """Ask StockRoom, the chat at the top of Home: how-to and stock questions in
    one place. The last few exchanges from the last day live in the session, which
    switching person replaces. "Delete chat" clears them, and keeps them for Undo
    until the next question."""
    if request.method != "POST":
        return redirect(reverse("stock:home") + "?ask")
    session = request.session
    if "new" in request.POST:
        session[UNDO_KEY] = recent_turns(session)
        session.pop(HISTORY_KEY, None)
        return render(request, "assistant/chat.html#deleted")  # empties the chat, offers Undo
    if "undo" in request.POST:
        session[HISTORY_KEY] = session.pop(UNDO_KEY, [])
        turns = "".join(render_to_string("assistant/chat.html#turn",
                                         {"q": q, "answer": a, "change": change, "links": links})
                        for q, a, change, links in chat_turns(session))
        return HttpResponse(turns + CLEAR_UNDO)

    history = recent_turns(session)
    session.pop(UNDO_KEY, None)
    question = request.POST.get("q", "").strip()[:MAX_QUESTION]
    if not question:
        return HttpResponse(status=204)
    if ai_limited(request):
        return _turn(request, question, LIMITED)
    turns = [turn for q, a, _ in history for turn in (("user", q), ("model", a))]
    try:
        reply = gemini.generate(_system_prompt(request.user), [*turns, ("user", question)],
                                tools=actions.declarations(request.user))
    except gemini.GeminiError:
        return _turn(request, question, "Sorry, I couldn't get an answer just now. Try again in a minute.")
    if isinstance(reply, list):  # it called an action: a card, buttons, or a plain answer if it can't be done
        reply = actions.propose(request.user, reply, getattr(reply, "text", ""))
    return _reply(request, question, reply)


def _reply(request, question, reply):
    """Add a turn to the chat and remember it: reply is a card (a dict), or an
    answer, which may have buttons under it."""
    session = request.session
    when = time.time()
    change, links = None, getattr(reply, "buttons", None) or None
    if isinstance(reply, dict):
        change = reply | {"id": uuid.uuid4().hex, "turn": when, "expires": when + CHANGE_AGE}
        session[CHANGES_KEY] = {**_changes(session), change["id"]: change}
        answer = f"Not confirmed, so nothing changed: {change['summary']}."
    else:
        # Gemini leans towards Markdown even when asked not to; bold is the usual leftover.
        answer = reply.replace("**", "")
    session[HISTORY_KEY] = [*recent_turns(session), [question, answer, when]][-HISTORY_TURNS:]
    kept = {str(turn[2]) for turn in session[HISTORY_KEY]}
    session[LINKS_KEY] = {
        **{t: b for t, b in session.get(LINKS_KEY, {}).items() if t in kept},
        **({str(when): links} if links else {}),
    }
    return _turn(request, question, answer, change, links)


def _turn(request, question, answer, change=None, links=None):
    """One exchange for htmx to add to the chat. On its own (not via _reply) it isn't
    remembered: a failed answer is gone on the next visit rather than kept in the chat."""
    response = render(request, "assistant/chat.html#turn",
                      {"q": question, "answer": answer, "change": change, "links": links})
    response.content += CLEAR_UNDO.encode()  # the old "Chat deleted. Undo" row is no longer true
    return response


@require_POST
@ai_required
@admin_required
def ask_receipt(request):
    """An invoice or receipt dropped into the chat, with the upload page's rules
    and the same pipeline: kept, read by parse_invoice, checked for a repeat,
    then a card that receives it on Confirm (assistant/actions.py). One AI call,
    or none for a CSV. What's printed on it can't ask for anything: it's read
    by parse_invoice, which has nothing it can call, and never reaches the chat's AI."""
    request.session.pop(UNDO_KEY, None)
    upload = request.FILES.get("invoice_file")
    if not upload:
        return _turn(request, "Sent a file", "No file was chosen. Pick an invoice or receipt and try again.")
    mime_type = upload.content_type or ""
    is_csv = mime_type in invoices.CSV_TYPES
    # Not its name: that's the sender's words, and the turn goes back to the AI with later questions.
    question = "Sent a CSV" if is_csv else "Sent a PDF" if mime_type == "application/pdf" else "Sent a photo"
    if not _invoice_file_ok(upload):
        return _turn(request, question, _wrong_file_text(upload))
    if not is_csv and ai_limited(request):
        return _turn(request, question, _limit_text(request))

    org = request.user.organisation
    data = upload.read()
    invoices.keep_document(org, request.user, upload.name, mime_type, data)  # before it's read, as on the upload page
    try:
        invoice, lines = invoices.parse_invoice(org, data, mime_type, upload.name)
    except gemini.GeminiError:
        return _turn(request, question, READ_FAILED)
    if original := invoices.find_original(invoice):
        day = timezone.localtime(original.created_at)
        return _reply(request, question, f"You brought this one in on {day.day} {day:%B}, so nothing's changed.")
    return _reply(request, question, actions.propose_receipt(request.user, invoice, lines))


@ai_required
@admin_required
def check_receipt(request, pid):
    """A receipt card's "Check it on the invoice page": the upload page's preview
    of it, for what the card can't do (match a line by hand, pick the supplier).
    The card leaves the chat, so the invoice can't be brought in from both."""
    session = request.session
    changes = _changes(session)
    change = changes.get(pid)
    if change is None or "receipt" not in change or "undo" in change:
        return redirect("stock:invoice_upload")
    del changes[pid]
    session[CHANGES_KEY] = changes
    _record(session, change, "Taken to the invoice page to check, so nothing changed here")
    org = request.user.organisation
    invoice = Invoice(organisation=org)
    return invoice_preview(request, invoice, fill_invoice(org, invoice, change["receipt"], QueryDict()))


# Confirm, Cancel and Undo on a card make no AI call, so they don't count towards AI_PER_HOUR.

@require_POST
@ai_required
def confirm_change(request, pid):
    """Confirm on a card: make the change, swap the card for its done state and
    show the usual toast with Undo. A second Confirm does nothing, and an id
    that's expired or was never shown changes nothing either."""
    session = request.session
    changes = _changes(session)
    change = changes.get(pid)
    if change is None:
        return render(request, "assistant/chat.html#change",
                      {"note": "That's expired, so nothing was changed. Ask again if you still need it."})
    if "undo" in change:
        return HttpResponse(status=204)
    done = actions.apply(request.user, change)
    if done is None:
        del changes[pid]
        session[CHANGES_KEY] = changes
        _record(session, change, "Couldn't be done, so nothing changed")
        return render(request, "assistant/chat.html#change",
                      {"note": "That can't be done now, so nothing was changed."})
    message, undo = done
    # "undo" marks it made, and is "" when there's nothing to undo (a receipt that received nothing).
    change |= {"done": True, "undo": undo, "expires": time.time() + UNDO_WINDOW.total_seconds()}
    session[CHANGES_KEY] = changes
    _record(session, change, "Done")
    response = render(request, "assistant/chat.html#done", {
        "change": change, "text": message, "undo": undo and reverse("assistant:undo_change", args=[pid]),
    })
    response["HX-Trigger"] = "stock-changed"  # Home's list redraws with the new numbers
    return response


@require_POST
@ai_required
def choose_item(request, pid, option):
    """A button on a card's "Which one?": run the card again with that one
    picked, which makes no AI call. A stale or made-up id leaves the card as it was."""
    session = request.session
    changes = _changes(session)
    change = changes.get(pid)
    if change is None:
        return render(request, "assistant/chat.html#change",
                      {"note": "That's expired, so nothing was changed. Ask again if you still need it."})
    which = change.get("which")
    if "undo" in change or not which or option not in {o["id"] for o in which["options"]}:
        return render(request, "assistant/chat.html#change", {"change": change})
    card = actions.build(request.user, change["calls"], {**change["picks"], which["key"]: option})
    if isinstance(card, str):
        del changes[pid]
        session[CHANGES_KEY] = changes
        _record(session, change, "Couldn't be done, so nothing changed")
        return render(request, "assistant/chat.html#change", {"note": card})
    change |= card
    session[CHANGES_KEY] = changes
    return render(request, "assistant/chat.html#change", {"change": change})


@require_POST
@ai_required
def cancel_change(request, pid):
    """Cancel on a card: nothing changes, and the chat says so."""
    session = request.session
    changes = _changes(session)
    if pid in changes:
        if "undo" in changes[pid]:
            return HttpResponse(status=204)  # already made; Undo is in the toast
        _record(session, changes.pop(pid), "Cancelled, so nothing changed")
        session[CHANGES_KEY] = changes
    return render(request, "assistant/chat.html#change", {"note": "Cancelled. Nothing was changed."})


@require_POST
@ai_required
def undo_change(request, pid):
    """The toast's Undo for a change made in the chat. It's the page's own undo
    (log_undo_batch for stock events, with log_undo's rules), then the chat's
    record of the change says it was undone."""
    session = request.session
    changes = _changes(session)
    change = changes.pop(pid, None)
    if change is None or not change.get("undo"):
        raise Http404
    path, _, query = change["undo"].partition("?")
    page_undo = resolve(path)
    request.GET = QueryDict(query)  # the page's undo reads the ids it's to reverse from here
    response = page_undo.func(request, *page_undo.args, **page_undo.kwargs)
    session[CHANGES_KEY] = changes
    _record(session, change, "Undone, so nothing changed")
    return response


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
        return _upload_failed(request, "", "No file was chosen. Pick an invoice or receipt and try again.")
    mime_type = upload.content_type or ""
    is_csv = mime_type in invoices.CSV_TYPES
    if not _invoice_file_ok(upload):
        return _upload_failed(request, upload.name, _wrong_file_text(upload))
    if not is_csv and ai_limited(request):
        return _upload_failed(request, upload.name, _limit_text(request))

    data = upload.read()
    # Kept before it's read, not after, so an invoice can never be parsed into
    # the practice's records without the original behind it (stock/nztax.py).
    invoices.keep_document(request.user.organisation, request.user, upload.name, mime_type, data)
    try:
        invoice, lines = invoices.parse_invoice(request.user.organisation, data, mime_type, upload.name)
    except gemini.GeminiError:
        return _upload_failed(request, upload.name, READ_FAILED)
    if invoices.find_original(invoice):
        invoices.ingest(invoice, lines, request.user)
        return redirect("stock:invoice_detail", invoice.pk)
    return invoice_preview(request, invoice, lines)


MAX_BATCH = 50


def _wrong_file_text(upload):
    """Why this particular file was turned away: too big, or the wrong sort."""
    if upload.size > MAX_UPLOAD:
        return (f"That file is {upload.size / 1024 / 1024:.1f} MB, and StockRoom takes up to "
                f"{MAX_UPLOAD // 1024 // 1024} MB. A photo taken on a lower setting, or the supplier's "
                "own PDF, will be small enough.")
    return ("StockRoom reads photos, PDFs and CSVs. That one is none of those, so there was nothing "
            "to read. Most suppliers will email a PDF if you ask.")


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
    if not uploads:
        return _upload_failed(request, "", "No files were chosen. Pick your invoices and try again.")
    if len(uploads) > MAX_BATCH:
        return _upload_failed(request, "", f"That's {len(uploads)} files, and StockRoom reads up to "
                                           f"{MAX_BATCH} at a time. Send them in smaller lots.")
    if bad := [upload for upload in uploads if not _invoice_file_ok(upload)]:
        return _upload_failed(request, ", ".join(upload.name for upload in bad[:3]), _wrong_file_text(bad[0]))
    read_by_ai = sum(upload.content_type not in invoices.CSV_TYPES for upload in uploads)
    if read_by_ai and ai_limited(request, calls=read_by_ai):
        return _upload_failed(request, "", _limit_text(request))

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
        messages.error(request, _limit_text(request))
    elif left.exists():
        invoices.start_batch(batch)
    return redirect("stock:invoice_batch", batch.pk)
