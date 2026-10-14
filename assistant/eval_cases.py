"""Example messages and what Ask StockRoom should do with them, for
`python manage.py ask_eval`. Items are the seeded demo practice's, spelled as
seed_demo spells them. See docs/ask-eval.md.

calls is every function the model should call, one dict each: "action" plus
the arguments that must match. "item" matches however the model spells it, as
long as it resolves to that item. No calls means a plain answer, and says is
text that answer must contain.
"""

from typing import NamedTuple

from assistant.views import MONEY_REFUSAL

GLOVES = "Nitrile gloves, size M"
TIPS = "Suction tips, disposable"
MASKS = "Face masks, Level 2"
NEEDLES = "Anaesthetic needles, 30G"
POUCHES = "Autoclave pouches 90x230"
PASTE_CUPS = "Prophy paste cups"
GAUZE = "Gauze squares, 5x5cm"
COMPOSITE = "Composite, A2 syringes"
LIGNOCAINE = "Lignocaine 2% w/ adrenaline"
BLADES = "Scalpel blades, #15"
SHEETS = "Rubber dam sheets"
MIRRORS = "Disposable mouth mirrors"
SHIELDS = "Face shields"
FILM = "Barrier film rolls"
PELLETS = "Cotton pellets"
SENSORS = "Digital sensor barriers"
BIBS = "Bib clips"
SUTURES = "Surgical sutures"


class Case(NamedTuple):
    message: str
    role: str  # "admin" or "assistant"
    calls: tuple = ()
    says: str = ""


def call(action, **args):
    return {"action": action, **args}


def used(item, qty):
    return call("log_used", item=item, qty=qty)


def out(item):
    return call("log_out", item=item)


def low(item):
    return call("log_low", item=item)


def count(item, qty):
    return call("set_count", item=item, qty=qty)


CASES = [
    # --- log_used: plain, then NZ phrasing, typos and numbers said in words ---
    Case("used 2 boxes of gloves", "assistant", (used(GLOVES, 2),)),
    Case("We used 1 box of nitrile gloves size M", "assistant", (used(GLOVES, 1),)),
    Case("used a box of med gloves", "assistant", (used(GLOVES, 1),)),
    Case("used 3 suction tips", "assistant", (used(TIPS, 3),)),
    Case("chewed through 20 suction tips this morning", "assistant", (used(TIPS, 20),)),
    Case("used two boxes of masks", "assistant", (used(MASKS, 2),)),
    Case("took 1 box of level 2 face masks", "admin", (used(MASKS, 1),)),
    Case("used 5 needles", "assistant", (used(NEEDLES, 5),)),
    Case("used 4 anaesthetic needles", "assistant", (used(NEEDLES, 4),)),
    Case("used 10 autoclave pouches", "assistant", (used(POUCHES, 10),)),
    Case("ran 30 pouches through the steriliser, so used 30 pouches", "assistant", (used(POUCHES, 30),)),
    Case("used 12 prophy paste cups", "assistant", (used(PASTE_CUPS, 12),)),
    Case("used 8 paste cups", "assistant", (used(PASTE_CUPS, 8),)),
    Case("used 2 packs of gauze", "assistant", (used(GAUZE, 2),)),
    Case("used a pack of gauze squares", "admin", (used(GAUZE, 1),)),
    Case("used 1 syringe of A2 composite", "assistant", (used(COMPOSITE, 1),)),
    Case("used 2 lignocaine cartridges", "assistant", (used(LIGNOCAINE, 2),)),
    Case("used 6 lignocaine", "assistant", (used(LIGNOCAINE, 6),)),
    Case("used 3 scalpel blades", "assistant", (used(BLADES, 3),)),
    Case("used 5 rubber dam sheets", "assistant", (used(SHEETS, 5),)),
    Case("used 4 mouth mirrors", "assistant", (used(MIRRORS, 4),)),
    Case("used 2 face shields", "assistant", (used(SHIELDS, 2),)),
    Case("used a roll of barrier film", "assistant", (used(FILM, 1),)),
    Case("used 3 packs of cotton pellets", "assistant", (used(PELLETS, 3),)),
    Case("used 15 sensor barriers", "assistant", (used(SENSORS, 15),)),
    Case("Used 2 boxs of glovs", "assistant", (used(GLOVES, 2),)),
    Case("useed 3 sucton tips", "assistant", (used(TIPS, 3),)),
    Case("log 2 gloves as used", "assistant", (used(GLOVES, 2),)),
    Case("Sofia used 2 boxes of gloves for the clinic's own use", "admin", (used(GLOVES, 2),)),
    # --- log_out: the last one has gone ---
    Case("chucked out the last of the 3-0 sutures", "assistant", (out(SUTURES),)),
    Case("were out of bibs", "assistant", (out(BIBS),)),
    Case("we've run out of 2.2 needles", "assistant", (out(NEEDLES),)),
    Case("used the last box of masks", "assistant", (out(MASKS),)),
    Case("no more suction tips", "assistant", (out(TIPS),)),
    Case("used the last of the gauze", "admin", (out(GAUZE),)),
    Case("we're all out of glovs", "assistant", (out(GLOVES),)),
    Case("finished the last roll of barrier film", "assistant", (out(FILM),)),
    Case("none left of the face shields", "assistant", (out(SHIELDS),)),
    Case("ran out of A2 composite", "admin", (out(COMPOSITE),)),
    # --- log_low: still some, but not for long ---
    Case("bibs are getting low", "assistant", (low(BIBS),)),
    Case("running low on lignocaine", "assistant", (low(LIGNOCAINE),)),
    Case("we're low on gloves", "assistant", (low(GLOVES),)),
    Case("nearly out of face shields", "assistant", (low(SHIELDS),)),
    Case("suction tips are low", "assistant", (low(TIPS),)),
    Case("not much gauze left", "assistant", (low(GAUZE),)),
    Case("pouches are running low", "admin", (low(POUCHES),)),
    Case("were getting low on paste cups", "assistant", (low(PASTE_CUPS),)),
    Case("sutures nearly gone", "assistant", (low(SUTURES),)),
    Case("runnin low on masks", "assistant", (low(MASKS),)),
    # --- set_count: an exact number on the shelf ---
    Case("there are 14 boxes of gloves on the shelf", "assistant", (count(GLOVES, 14),)),
    Case("counted 6 boxes of masks", "assistant", (count(MASKS, 6),)),
    Case("we have 3 composite syringes left", "assistant", (count(COMPOSITE, 3),)),
    Case("stocktake: 120 autoclave pouches", "admin", (count(POUCHES, 120),)),
    Case("just counted 25 suction tips", "assistant", (count(TIPS, 25),)),
    Case("there's 8 packs of cotton pellets", "assistant", (count(PELLETS, 8),)),
    Case("lignocaine count is 40", "admin", (count(LIGNOCAINE, 40),)),
    Case("shelf has 2 rolls of barrier film", "assistant", (count(FILM, 2),)),
    Case("sutures: 12 on the shelf", "assistant", (count(SUTURES, 12),)),
    Case("I counted twenty needles", "assistant", (count(NEEDLES, 20),)),
    # --- several changes in one message ---
    Case("used 2 bibs and a box of gloves, and suction tips are low", "assistant",
         (used(BIBS, 2), used(GLOVES, 1), low(TIPS))),
    Case("used 3 pouches and out of face shields", "assistant", (used(POUCHES, 3), out(SHIELDS))),
    Case("counted 10 masks and 5 boxes of gloves", "admin", (count(MASKS, 10), count(GLOVES, 5))),
    # --- no action: questions, chit-chat, clinical, and money for an assistant ---
    Case("How do I log what I've used?", "assistant"),
    Case("How do I receive a delivery?", "assistant"),
    Case("What's running low?", "assistant"),
    Case("How many boxes of gloves do we have?", "assistant"),
    Case("What should I order this week?", "admin"),
    Case("Where could we save money?", "admin"),
    Case("Kia ora", "assistant"),
    Case("Thanks, that's great", "assistant"),
    Case("What's the best way to treat a dry socket?", "assistant"),
    Case("What dose of lignocaine is safe for a child?", "assistant"),
    Case("What do we spend the most on?", "assistant", says=MONEY_REFUSAL),
    Case("How much did we spend at Henry Schein this month?", "assistant", says=MONEY_REFUSAL),
]
