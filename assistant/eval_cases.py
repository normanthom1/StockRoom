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
DAPPEN = "Dappen dishes"
PAPER = "Articulating paper"
FLOSS = "Dental floss spools"
TRAYS = "Disposable impression trays"
SHARPS = "Sharps containers"
INDEPENDENT = "Independent Dental Supplies"


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


def receive_order(supplier):
    return call("receive_order", supplier=supplier)


def receive_line(item, **args):
    return call("receive_line", item=item, **args)


def add_to_list(item):
    return call("add_to_reorder", item=item)


def take_off_list(item):
    return call("remove_from_reorder", item=item)


def order_size(item, qty):
    return call("set_order_size", item=item, qty=qty)


def ordered(item, **args):
    return call("mark_ordered", item=item, **args)


def supplier_ordered(supplier):
    return call("mark_supplier_ordered", supplier=supplier)


def switch(item, supplier):
    return call("switch_supplier", item=item, supplier=supplier)


def email(supplier):
    return call("write_order_email", supplier=supplier)


def add_item(**args):
    return call("add_item", **args)


def edit_item(item, **args):
    return call("edit_item", item=item, **args)


def set_price(item, price):
    return call("set_price", item=item, price=price)


def archive(item):
    return call("archive_item", item=item)


def unarchive(item):
    return call("unarchive_item", item=item)


def add_backup(item, supplier):
    return call("add_backup_supplier", item=item, supplier=supplier)


def remove_backup(item, supplier):
    return call("remove_backup_supplier", item=item, supplier=supplier)


def add_supplier(**args):
    return call("add_supplier", **args)


def edit_supplier(supplier, **args):
    return call("edit_supplier", supplier=supplier, **args)


def page(name, **args):
    return call("open_page", page=name, **args)


def download(file, **args):
    return call("download", file=file, **args)


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
    # --- receive_order and receive_line: a delivery has arrived ---
    Case("The Henry Schein order came", "assistant", (receive_order("Henry Schein"),)),
    Case("Dentsply's delivery is here", "assistant", (receive_order("Dentsply"),)),
    Case("henry schein just dropped off our order", "assistant", (receive_order("Henry Schein"),)),
    Case("got the Dentsply order, all of it", "admin", (receive_order("Dentsply"),)),
    Case("the order from Henry Schein has arrived", "admin", (receive_order("Henry Schein"),)),
    # Only items with an open order in the demo practice (seed_demo): sheets, pouches and film.
    Case("the rubber dam sheets are here", "assistant", (receive_line(SHEETS),)),
    Case("barrier film arrived", "assistant", (receive_line(FILM),)),
    Case("the autoclave pouches came in", "assistant", (receive_line(POUCHES),)),
    Case("only 60 of the 110 autoclave pouches arrived, the rest are on back-order", "assistant",
         (receive_line(POUCHES, qty=60),)),
    Case("got 5 of the 10 rubber dam sheets, rest coming later", "assistant", (receive_line(SHEETS, qty=5),)),
    Case("only 1 roll of barrier film turned up", "assistant", (receive_line(FILM, qty=1),)),
    Case("rubber dam sheets are here and they charged $1.30 each", "admin", (receive_line(SHEETS, price=1.3),)),
    Case("barrier film came, $19 a roll this time", "admin", (receive_line(FILM, price=19),)),
    Case("the pouches came but they were 15 cents each, only 100 of them", "admin",
         (receive_line(POUCHES, qty=100, price=0.15),)),
    Case("the sheets arrived", "assistant", (receive_line(SHEETS),)),
    # --- add_to_reorder: a manager adds, an assistant asks the manager ---
    # Items that are fine in the demo practice, so they're not already on the list (the model says so if they are).
    Case("add cotton pellets to the reorder list", "admin", (add_to_list(PELLETS),)),
    Case("put the dappen dishes on the list", "admin", (add_to_list(DAPPEN),)),
    Case("we need more articulating paper, add it to the order list", "admin", (add_to_list(PAPER),)),
    Case("add floss and cotton pellets to the reorder list", "admin", (add_to_list(FLOSS), add_to_list(PELLETS))),
    Case("chuck the sharps containers on the list", "admin", (add_to_list(SHARPS),)),
    Case("ask the manager to order more cotton pellets", "assistant", (add_to_list(PELLETS),)),
    Case("we need more dental floss ordered", "assistant", (add_to_list(FLOSS),)),
    Case("can we get more articulating paper ordered?", "assistant", (add_to_list(PAPER),)),
    Case("we're going to need more dappen dishes, can the manager order some?", "assistant",
         (add_to_list(DAPPEN),)),
    Case("can u ask for more sharps containers to be ordered", "assistant", (add_to_list(SHARPS),)),
    # --- remove_from_reorder ---
    Case("take gloves off the reorder list", "admin", (take_off_list(GLOVES),)),
    Case("we don't need the gauze ordered after all", "admin", (take_off_list(GAUZE),)),
    Case("remove bib clips from the order list", "admin", (take_off_list(BIBS),)),
    Case("scrap the sutures from the list", "admin", (take_off_list(SUTURES),)),
    # --- set_order_size: how many at a time, from now on ---
    Case("we order bibs in 10s", "admin", (order_size(BIBS, 10),)),
    Case("make the gloves order 10 boxes from now on", "admin", (order_size(GLOVES, 10),)),
    Case("order size for suction tips should be 100", "admin", (order_size(TIPS, 100),)),
    Case("always order face masks in fives", "admin", (order_size(MASKS, 5),)),
    # --- mark_ordered: it has been ordered ---
    Case("I ordered the gloves", "admin", (ordered(GLOVES),)),
    Case("ordered 10 boxes of gloves", "admin", (ordered(GLOVES, qty=10),)),
    Case("we've ordered the suction tips, 200 of them", "admin", (ordered(TIPS, qty=200),)),
    Case("ordered the masks and the face shields", "admin", (ordered(MASKS), ordered(SHIELDS))),
    Case("just rang through an order for 3 syringes of composite", "admin", (ordered(COMPOSITE, qty=3),)),
    # --- mark_supplier_ordered: everything from one supplier ---
    Case("mark the Henry Schein order as sent", "admin", (supplier_ordered("Henry Schein"),)),
    Case("we've ordered everything from Dentsply", "admin", (supplier_ordered("Dentsply"),)),
    Case("the Henry Schein order has gone through", "admin", (supplier_ordered("Henry Schein"),)),
    # --- switch_supplier: to a backup ---
    Case("order the gloves from Independent Dental Supplies instead", "admin",
         (switch(GLOVES, "Independent Dental Supplies"),)),
    Case("switch suction tips to Independent Dental Supplies", "admin",
         (switch(TIPS, "Independent Dental Supplies"),)),
    Case("get the gauze from Independent Dental Supplies from now on", "admin",
         (switch(GAUZE, "Independent Dental Supplies"),)),
    # --- write_order_email: the Reorder list's email, for the manager to send ---
    Case("write the email to Dentsply", "admin", (email("Dentsply"),)),
    Case("draft the order email for Henry Schein", "admin", (email("Henry Schein"),)),
    Case("I need to email Dentsply our order", "admin", (email("Dentsply"),)),
    # --- an assistant asking for the manager's jobs is told, and gets no function for it ---
    Case("mark the Henry Schein order as sent", "assistant", says="manager"),
    Case("make the gloves order 10 boxes", "assistant", says="manager"),
    Case("switch the gloves to Independent Dental Supplies", "assistant", says="manager"),
    Case("write the email to Dentsply", "assistant", says="manager"),
    # --- add_item: only what they said is passed on (the name is left to the model's own spelling) ---
    Case("add 3M Filtek Z350 in A2 from Henry Schein, $85 a syringe", "admin",
         (add_item(unit="syringe", supplier="Henry Schein", price=85),)),
    Case("we've started using prophy angles from Dentsply, they come in boxes", "admin",
         (add_item(unit="box", supplier="Dentsply"),)),
    Case("add a new item, ProRoot MTA, from Dentsply. It's a kit and costs $120", "admin",
         (add_item(unit="kit", supplier="Dentsply", price=120),)),
    Case("new item: saliva ejector tips, packs of 100, from Henry Schein", "admin",
         (add_item(unit="pack", supplier="Henry Schein"),)),
    # --- edit_item: name, unit or preferred supplier ---
    Case("rename the bib clips to Patient bib clips", "admin", (edit_item(BIBS, new_name="Patient bib clips"),)),
    Case("the gloves come from Dentsply now", "admin", (edit_item(GLOVES, new_supplier="Dentsply"),)),
    Case("we count suction tips in packs now, not tips", "admin", (edit_item(TIPS, new_unit="pack"),)),
    # --- set_price ---
    Case("gloves are $7.20 a box now", "admin", (set_price(GLOVES, 7.2),)),
    Case("the composite is $85 a syringe", "admin", (set_price(COMPOSITE, 85),)),
    Case("face masks went up to $14", "admin", (set_price(MASKS, 14),)),
    Case("lignocaine is down to 90 cents a cartridge", "admin", (set_price(LIGNOCAINE, 0.9),)),
    # --- archive_item and unarchive_item ---
    Case("archive the old impression trays", "admin", (archive(TRAYS),)),
    Case("we don't use the sharps containers any more, archive them", "admin", (archive(SHARPS),)),
    Case("bring back the impression trays", "admin", (unarchive(TRAYS),)),
    Case("restore the impression trays, we need them again", "admin", (unarchive(TRAYS),)),
    # --- add_backup_supplier and remove_backup_supplier ---
    Case("add Dentsply as a backup for gauze", "admin", (add_backup(GAUZE, "Dentsply"),)),
    Case("Independent Dental Supplies can supply the face shields too", "admin",
         (add_backup(SHIELDS, INDEPENDENT),)),
    Case("Independent Dental Supplies doesn't do the face masks any more", "admin",
         (remove_backup(MASKS, INDEPENDENT),)),
    # --- add_supplier, edit_supplier, archive_supplier and unarchive_supplier ---
    Case("add a supplier called Dental Direct, phone 09 555 0101", "admin",
         (add_supplier(name="Dental Direct", phone="09 555 0101"),)),
    Case("new supplier Southern Cross, their email is orders@southerncross.example", "admin",
         (add_supplier(name="Southern Cross", email="orders@southerncross.example"),)),
    Case("Henry Schein takes 3 days now", "admin", (edit_supplier("Henry Schein", lead_days=3),)),
    Case("Dentsply's new number is 0800 111 222", "admin", (edit_supplier("Dentsply", phone="0800 111 222"),)),
    Case("Dentsply deliver in 10 days these days", "admin", (edit_supplier("Dentsply", lead_days=10),)),
    Case("archive Independent Dental Supplies", "admin", (call("archive_supplier", supplier=INDEPENDENT),)),
    Case("bring back Independent Dental Supplies", "admin", (call("unarchive_supplier", supplier=INDEPENDENT),)),
    # --- starting a stocktake is the button to its page ---
    Case("start a stocktake", "admin", (page("stocktake"),)),
    Case("we want to count everything, how do we do a stocktake?", "admin", (page("stocktake"),)),
    # --- an assistant asking for a manager's job is told, and has no function for it ---
    Case("add a new item called prophy angles from Dentsply", "assistant", says="manager"),
    Case("archive the old impression trays", "assistant", says="manager"),
    Case("Henry Schein takes 3 days now", "assistant", says="manager"),
    Case("gloves are $7.20 a box now", "assistant", says="manager"),
    # --- open_page and download: take them there, nothing changes ---
    Case("where do I change a supplier's phone number?", "admin", (page("suppliers"),)),
    Case("show me spending by supplier", "admin", (page("spending"),)),
    Case("what did we spend at Henry Schein this month?", "admin", (page("spending", period="month"),)),
    Case("how much have we spent this year?", "admin", (page("spending", period="year"),)),
    Case("show me the tax year page", "admin", (page("tax_year"),)),
    Case("who did what yesterday?", "admin", (page("activity"),)),
    Case("take me to the team page", "admin", (page("team"),)),
    Case("I want to add something from the catalogue", "admin", (page("catalogue"),)),
    Case("let's do a stocktake", "admin", (page("stocktake"),)),
    Case("where do I edit an item's price?", "admin", (page("stock"),)),
    Case("take me to the reorder list", "assistant", (page("reorder_list"),)),
    Case("where do I receive a delivery?", "assistant", (page("deliveries"),)),
    Case("go to log usage", "assistant", (page("log_usage"),)),
    Case("take me home", "assistant", (page("home"),)),
    Case("open the gloves page", "assistant", (page("item", item=GLOVES),)),
    Case("show me the page for suction tips", "admin", (page("item", item=TIPS),)),
    Case("download this year's tax CSV", "admin", (download("tax_year"),)),
    Case("download the item list", "admin", (download("items"),)),
    Case("export every use, count and delivery", "admin", (download("stock_events"),)),
    Case("give me the order lines as a spreadsheet", "admin", (download("order_lines"),)),
    Case("show me spending", "assistant", says=MONEY_REFUSAL),
    Case("download the tax CSV", "assistant", says=MONEY_REFUSAL),
    Case("open the team page", "assistant", says=MONEY_REFUSAL),
    # --- no action: questions, chit-chat, clinical, and money for an assistant ---
    Case("Who asked for gauze to go on the list?", "assistant"),
    Case("How long does Henry Schein take to deliver?", "assistant"),
    Case("What's running low?", "assistant"),
    Case("What's on the reorder list?", "admin"),
    Case("When will the Henry Schein order arrive?", "admin"),
    Case("Who usually orders the gloves?", "assistant"),
    Case("Which supplier do we get gloves from?", "admin"),
    Case("How do I add an item?", "admin"),
    Case("How long does Dentsply take to deliver?", "admin"),
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
