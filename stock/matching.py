"""Matching a product name to one of the practice's items, so "glove" and
"Gloves", or "Syringe 5ml" and "5 ml syringe", never become two products.

Steps, first hit wins: the same supplier code, a name matched before (an
ItemAlias), the same normalised name, the same after synonyms, a close
spelling, and only when still unsure and AI is on, Gemini embeddings. A sure
match is applied and remembered as an ItemAlias, which can be undone for
UNDO_DAYS; anything less is a suggestion for someone to confirm.
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from assistant import gemini

from .catalogue_data import SYNONYMS
from .models import InvoiceLine, Item, ItemAlias

SURE = 0.95  # at or above this, a match is applied without asking
UNSURE_FLOOR = 0.60  # below this, a name isn't suggested as anything
TIE = 0.02  # candidates this close are a tie, settled by code, supplier, then price
SIZE_CLASH = 0.8  # "Syringe 5ml" and "Syringe 10ml" spell alike but aren't the same product
SEMANTIC_CANDIDATES = 5
UNDO_DAYS = 30

STOPWORDS = {"a", "an", "the", "of", "for", "and", "with"}
UNITS = [
    (r"millilit(?:re|er)s?|mls?|cc", "ml"),
    (r"lit(?:re|er)s?|l", "l"),
    (r"milligrams?|mg", "mg"),
    (r"kilograms?|kg", "kg"),
    (r"grams?|gm|g", "g"),
    (r"mm", "mm"),
    (r"cm", "cm"),
]
PACK = r"(?:box|bx|pack|pk|packet|bag|tub|roll)"
PACKS = [
    re.compile(rf"\b{PACK}\s+of\s+(\d+)\b"),  # box of 100
    re.compile(rf"\b(\d+)\s*(?:/|per\s+){PACK}\b"),  # 100/box, 100 per pack
    re.compile(r"(?:^|(?<=\s))x\s*(\d+)\b"),  # x100, but not the x in 57x130
    re.compile(r"\b(\d+)\s*(?:pk|packs?|pcs|pieces?|ct)\b"),  # 100pk, 100 pieces
]


def _words(name):
    """The name as lowercase singular words, units and pack sizes written one way, stopwords gone."""
    text = unicodedata.normalize("NFKC", name).lower()
    for pattern, unit in UNITS:
        text = re.sub(rf"(\d+(?:\.\d+)?)\s*(?:{pattern})\b", rf"\g<1>{unit}", text)
    for pattern in PACKS:
        text = pattern.sub(r" \g<1>pk ", text)
    text = re.sub(r"[^\w.]+|_|(?<!\d)\.|\.(?!\d)", " ", text)  # keeps the point in 2.5ml
    return [_singular(word) for word in text.split() if word not in STOPWORDS]


def _singular(word):
    if len(word) <= 3 or any(c.isdigit() for c in word) or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    return word.removesuffix("s")


def normalize(name):
    """A key that's the same for names differing only in case, punctuation,
    plurals, unit spelling, filler words or word order."""
    return " ".join(sorted(_words(name)))


_SYNONYMS = {tuple(_words(other)): _words(main) for other, main in SYNONYMS.items()}
_LONGEST_SYNONYM = max(len(words) for words in _SYNONYMS)


def synonym_key(name):
    """normalize(), with other names for a thing swapped for the one it's matched on."""
    words, out, i = _words(name), [], 0
    while i < len(words):
        for n in range(min(_LONGEST_SYNONYM, len(words) - i), 0, -1):
            if tuple(words[i:i + n]) in _SYNONYMS:
                out += _SYNONYMS[tuple(words[i:i + n])]
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(sorted(out))


def similarity(a, b):
    """0 to 1: the better of a plain spelling ratio and a token-set ratio on two
    keys. Pairs with no word in common that can't reach UNSURE_FLOOR score 0,
    which skips most of the work across a whole stock list."""
    spelling = SequenceMatcher(None, a, b)
    words_a, words_b = set(a.split()), set(b.split())
    common = " ".join(sorted(words_a & words_b))
    if not common:
        return spelling.ratio() if spelling.quick_ratio() >= UNSURE_FLOOR else 0.0
    with_a = f"{common} {' '.join(sorted(words_a - words_b))}".strip()
    with_b = f"{common} {' '.join(sorted(words_b - words_a))}".strip()
    return max(spelling.ratio(), *(SequenceMatcher(None, x, y).ratio() for x, y in
                                   ((common, with_a), (common, with_b), (with_a, with_b))))


def _sizes(key):
    return {word for word in key.split() if any(c.isdigit() for c in word)}


@dataclass
class Match:
    item: Item | None
    confidence: float
    method: str = ""  # an ItemAlias.Method, blank when nothing matched
    candidates: list = field(default_factory=list, repr=False)  # unsure items worth an embedding check

    @property
    def band(self):
        """sure: applied without asking. likely or check: a suggestion to confirm. Blank: no match."""
        if self.item is None:
            return ""
        if self.confidence >= SURE and self.method != ItemAlias.Method.SEMANTIC:
            return "sure"
        if self.confidence >= settings.PRODUCT_FUZZY_THRESHOLD or self.method == ItemAlias.Method.SEMANTIC:
            return "likely"
        return "check"

    @property
    def label(self):
        words = {"sure": "Sure match", "likely": "Likely match", "check": "Check this"}.get(self.band, "")
        return f"{words} – {round(self.confidence * 100)}%" if words else ""


class Matcher:
    """Matches names against one practice's active items. Build one per
    invoice or import, so the items are only read and normalised once."""

    def __init__(self, organisation):
        self.organisation = organisation
        self.items = [
            (item, normalize(item.name), synonym_key(item.name))
            for item in Item.objects.for_org(organisation).filter(is_active=True)
        ]
        by_pk = {item.pk: item for item, _, _ in self.items}
        live, self.not_same = {}, set()
        for alias in ItemAlias.objects.for_org(organisation).filter(item__in=list(by_pk)).order_by("created_at"):
            if alias.reverted_at is None:
                live[alias.key] = by_pk[alias.item_id]
            else:  # an undone merge: that name isn't that item
                self.not_same.add((alias.key, alias.item_id))
        self.aliases = live
        self.not_same -= {(key, item.pk) for key, item in live.items()}

    def match_all(self, queries):
        """queries: dicts of name, and optionally sku, supplier and price. One
        Match each, with a single Gemini call for all the unsure ones."""
        matches = [self.match(**query) for query in queries]
        unsure = [(query, match) for query, match in zip(queries, matches, strict=True) if match.candidates]
        if unsure and settings.AI_API_KEY:
            self._check_meaning(unsure)
        return matches

    def match(self, name, *, sku="", supplier=None, price=None):
        key, syn = normalize(name), synonym_key(name)
        allowed = [(item, k, s) for item, k, s in self.items if (key, item.pk) not in self.not_same]

        def pick(found, confidence, method):
            return self._tie_break(found, confidence, method, sku, supplier, price)

        if sku and supplier:
            found = [item for item, _, _ in self.items
                     if item.supplier_id == supplier.pk and item.supplier_sku.lower() == sku.lower()]
            if found:
                return pick([(item, 1.0) for item in found], 1.0, ItemAlias.Method.SKU)
        if key in self.aliases:
            return Match(self.aliases[key], 1.0, ItemAlias.Method.ALIAS)
        if found := [(item, 1.0) for item, k, _ in allowed if k == key]:
            return pick(found, 1.0, ItemAlias.Method.EXACT)
        if found := [(item, 0.98) for item, _, s in allowed if s == syn]:
            return pick(found, 0.98, ItemAlias.Method.SYNONYM)

        scored, candidates = [], []
        for item, _, item_syn in allowed:
            score = similarity(syn, item_syn)
            if score < UNSURE_FLOOR:
                continue
            sizes, item_sizes = _sizes(syn), _sizes(item_syn)
            clash = bool(sizes and item_sizes and sizes != item_sizes)
            if clash:
                score *= SIZE_CLASH
            elif set(syn.split()) < set(item_syn.split()) or set(item_syn.split()) < set(syn.split()):
                score = min(score, SURE - 0.01)  # "gloves" is like "Nitrile gloves, size M", but not surely it
            scored.append((item, score))
            if not clash and score < settings.PRODUCT_FUZZY_THRESHOLD:
                candidates.append((score, item))
        best = max((score for _, score in scored), default=0.0)
        if best < UNSURE_FLOOR:
            return Match(None, best)
        match = pick([(item, score) for item, score in scored if score >= best - TIE], best, ItemAlias.Method.FUZZY)
        if match.band not in ("sure", "likely"):
            match.candidates = [item for _, item in sorted(candidates, key=lambda c: -c[0])[:SEMANTIC_CANDIDATES]]
        return match

    def _tie_break(self, found, confidence, method, sku, supplier, price):
        def preference(entry):
            item = entry[0]
            return (
                not (sku and item.supplier_sku.lower() == sku.lower()),
                not (supplier and item.supplier_id == supplier.pk),
                abs(item.price - price) if item.price is not None and price is not None else math.inf,
            )

        item = min(found, key=preference)[0]
        if len(found) > 1:
            confidence = min(confidence, SURE - 0.01)  # two items fit about as well: ask, don't guess
        return Match(item, confidence, method)

    def _check_meaning(self, unsure):
        """Embedding similarity for names spelling couldn't settle. Never a
        sure match, only a suggestion. Item vectors are cached on the item."""
        items = {item.pk: item for _, match in unsure for item in match.candidates}
        stale = [item for item in items.values()
                 if (item.name_embedding or {}).get("key") != normalize(item.name)]
        names = [query["name"] for query, _ in unsure]
        try:
            vectors = gemini.embed(names + [item.name for item in stale])
        except gemini.GeminiError:
            return  # spelling alone, then; the suggestions just stay as they were
        for item, vector in zip(stale, vectors[len(names):], strict=True):
            item.name_embedding = {"key": normalize(item.name), "values": vector}
            Item.objects.filter(pk=item.pk).update(name_embedding=item.name_embedding)
        for (_, match), vector in zip(unsure, vectors[:len(names)], strict=True):
            score, item = max(((_cosine(vector, item.name_embedding["values"]), item) for item in match.candidates),
                              key=lambda pair: pair[0])
            if score >= settings.PRODUCT_EMBEDDING_THRESHOLD:
                match.item, match.confidence, match.method = item, score, ItemAlias.Method.SEMANTIC


def _cosine(a, b):
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / size if size else 0.0


def record_merge(match, *, name, user, source, invoice=None):
    """Remember that name is match.item, so it matches straight away next time.
    Running it again for the same name adds nothing. A code or alias match is
    already remembered, and a name that only differs in case isn't a merge."""
    if match.method in (ItemAlias.Method.SKU, ItemAlias.Method.ALIAS) or name.strip().lower() == match.item.name.lower():
        return None
    alias, _ = ItemAlias.objects.get_or_create(
        organisation=user.organisation,
        key=normalize(name),
        reverted_at=None,
        defaults={"item": match.item, "raw_name": name.strip()[:200], "method": match.method,
                  "confidence": match.confidence, "source": source, "source_invoice": invoice, "created_by": user},
    )
    return alias


def undo_merge(alias, user):
    """That name isn't that item after all: stop matching it, and unmatch the
    invoice lines it matched. False if it's already undone or too old."""
    if alias.reverted_at or alias.created_at < timezone.now() - timedelta(days=UNDO_DAYS):
        return False
    with transaction.atomic():
        alias.reverted_at, alias.reverted_by = timezone.now(), user
        alias.save(update_fields=["reverted_at", "reverted_by"])
        if alias.source_invoice_id:
            lines = InvoiceLine.objects.filter(invoice_id=alias.source_invoice_id, item=alias.item)
            InvoiceLine.objects.filter(
                pk__in=[line.pk for line in lines if normalize(line.description or line.sku) == alias.key]
            ).update(item=None)
    return True
