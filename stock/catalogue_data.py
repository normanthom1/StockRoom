"""The shared stock catalogue: products NZ dental practices commonly order,
and which NZ suppliers sell that kind of product. Loaded into the database by
migrations (see stock.catalogue.sync_catalogue); edit here, then add a
migration that calls sync_catalogue again.

Suppliers are matched to products by the ranges each one lists on its own
website (checked September 2026), not product by product, so the catalogue
tells practices to check with their suppliers. Order matters: a supplier
listed first is the default pick when a practice uses none of them yet.
"""

HENRY_SCHEIN = "Henry Schein"
INDEPENDENT = "Independent Dental Supplies"
ALURO = "Aluro Healthcare"
DE_HEALTHCARE = "DE Healthcare"
DENTSPLY = "Dentsply"

SUPPLIERS = {
    # The full-range distributor most practices already deal with.
    HENRY_SCHEIN: "https://www.henryschein.co.nz/",
    # NZ-owned: anaesthesia, burs, disposables, endo, impression, infection control, PPE,
    # preventative, restorative, rubber dam, surgical, whitening, x-ray.
    INDEPENDENT: "https://independentdental.co.nz/",
    # Anaesthetics, infection control, endo, preventative, restorative, cements,
    # consumables, impression, burs, suction, x-ray, whitening.
    ALURO: "https://www.aluro.co.nz/",
    # Infection control and disposables only: gloves, masks, pouches, wipes, bibs,
    # alginate, anaesthetic accessories. No cartridges or restorative materials.
    DE_HEALTHCARE: "https://dehealthcare.co.nz/",
    # Dentsply Sirona sells its own brands direct through its NZ online shop.
    DENTSPLY: "https://www.dentsplysirona.com/en-nz",
}

# Category: (who sells that range, [(product, unit, extra suppliers for this product)]).
# Units are singular, like Item.unit.
CATALOGUE = {
    "Gloves and PPE": ([HENRY_SCHEIN, INDEPENDENT, DE_HEALTHCARE, ALURO], [
        ("Nitrile gloves, size XS", "box", []),
        ("Nitrile gloves, size S", "box", []),
        ("Nitrile gloves, size M", "box", []),
        ("Nitrile gloves, size L", "box", []),
        ("Surgical gloves, sterile", "pair", []),
        ("Face masks, Level 2", "box", []),
        ("Face masks, Level 3", "box", []),
        ("Face shields", "shield", []),
        ("Safety glasses, patient", "pair", []),
        ("Disposable gowns", "gown", []),
        ("Bouffant caps", "cap", []),
    ]),
    "Infection control and sterilising": ([HENRY_SCHEIN, INDEPENDENT, DE_HEALTHCARE, ALURO], [
        ("Autoclave pouches 57x130", "pouch", []),
        ("Autoclave pouches 90x230", "pouch", []),
        ("Autoclave pouches 190x330", "pouch", []),
        ("Sterilisation indicator strips", "strip", []),
        ("Surface disinfectant wipes", "canister", []),
        ("Surface disinfectant spray", "bottle", []),
        ("Hand sanitiser", "bottle", []),
        ("Instrument cleaning solution", "bottle", []),
        ("Barrier film rolls", "roll", []),
        ("Headrest covers", "cover", []),
        ("Sharps containers", "container", []),
    ]),
    "Chairside disposables": ([HENRY_SCHEIN, INDEPENDENT, DE_HEALTHCARE], [
        ("Suction tips, disposable", "tip", [ALURO]),
        ("Saliva ejectors", "ejector", [ALURO]),
        ("Air/water syringe tips", "tip", []),
        ("Patient bibs", "bib", []),
        ("Bib clips", "clip", []),
        ("Patient cups", "cup", []),
        ("Cotton rolls", "roll", []),
        ("Cotton pellets", "pack", []),
        ("Gauze squares, 5x5cm", "pack", []),
        ("Disposable mouth mirrors", "mirror", []),
        ("Dappen dishes", "dish", []),
        ("Microbrush applicators", "applicator", []),
    ]),
    "Anaesthetic": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Lignocaine 2% w/ adrenaline", "cartridge", []),
        ("Articaine 4% w/ adrenaline", "cartridge", []),
        ("Prilocaine 3% w/ felypressin", "cartridge", []),
        ("Mepivacaine 3% plain", "cartridge", []),
        ("Anaesthetic needles, 30G", "needle", [DE_HEALTHCARE]),
        ("Anaesthetic needles, 27G long", "needle", [DE_HEALTHCARE]),
        ("Topical anaesthetic gel", "tub", []),
    ]),
    "Restorative": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Composite, A1 syringes", "syringe", [DENTSPLY]),
        ("Composite, A2 syringes", "syringe", [DENTSPLY]),
        ("Composite, A3 syringes", "syringe", [DENTSPLY]),
        ("Composite, A3.5 syringes", "syringe", [DENTSPLY]),
        ("Flowable composite, A2", "syringe", [DENTSPLY]),
        ("Universal bonding agent", "bottle", [DENTSPLY]),
        ("Etchant gel, 37%", "syringe", []),
        ("Glass ionomer cement", "kit", []),
        ("Resin-modified glass ionomer", "kit", []),
        ("Calcium hydroxide liner", "kit", []),
        ("Temporary filling material", "tube", []),
        ("Temporary crown material", "cartridge", [DENTSPLY]),
        ("Temporary cement", "tube", []),
        ("Resin cement", "syringe", []),
        ("Fissure sealant", "kit", [DENTSPLY]),
        ("Matrix bands, assorted", "pack", []),
        ("Sectional matrix bands", "pack", []),
        ("Wooden wedges, assorted", "pack", []),
        ("Articulating paper", "book", []),
        ("Rubber dam sheets", "sheet", []),
        ("Rubber dam clamps", "clamp", []),
    ]),
    "Burs and polishing": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Diamond burs, assorted", "pack", []),
        ("Tungsten carbide burs, assorted", "pack", []),
        ("Polishing discs", "pack", []),
        ("Polishing strips", "pack", []),
        ("Composite polishing points", "pack", []),
    ]),
    "Endodontics": ([HENRY_SCHEIN, DENTSPLY, INDEPENDENT, ALURO], [
        ("Endodontic files, assorted", "file", []),
        ("Rotary files", "pack", []),
        ("Gutta percha points", "box", []),
        ("Paper points, assorted", "box", []),
        ("Root canal sealer", "tube", []),
        ("Calcium hydroxide paste", "syringe", []),
        ("Irrigation syringes", "syringe", []),
        ("Irrigation needles, side-vented", "needle", []),
        ("Sodium hypochlorite solution", "bottle", []),
        ("EDTA gel", "syringe", []),
    ]),
    "Impressions": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Alginate powder", "bag", [DENTSPLY, DE_HEALTHCARE]),
        ("Silicone impression material, light body", "cartridge", [DENTSPLY]),
        ("Silicone impression putty", "tub", [DENTSPLY]),
        ("Impression mixing tips", "tip", []),
        ("Tray adhesive", "bottle", []),
        ("Disposable impression trays", "tray", []),
        ("Bite registration paste", "cartridge", []),
    ]),
    "Prevention and hygiene": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Prophy paste cups", "cup", [DENTSPLY]),
        ("Polishing paste", "tub", [DENTSPLY]),
        ("Disposable prophy angles", "angle", [DENTSPLY]),
        ("Fluoride varnish", "dose", [DENTSPLY]),
        ("Disclosing tablets", "pack", []),
        ("Dental floss spools", "spool", []),
        ("Interdental brushes", "pack", []),
        ("Patient toothbrushes", "brush", []),
        ("Take-home whitening gel", "syringe", []),
        ("Ultrasonic scaler tips", "tip", []),
    ]),
    "Surgical": ([HENRY_SCHEIN, INDEPENDENT], [
        ("Surgical sutures", "suture", []),
        ("Scalpel blades, #15", "blade", []),
        ("Scalpel blades, #12", "blade", []),
        ("Haemostatic gauze", "pack", []),
        ("Sterile saline", "bottle", []),
        ("Dry socket paste", "jar", []),
        ("Surgical aspirator tips", "tip", []),
    ]),
    "X-ray": ([HENRY_SCHEIN, INDEPENDENT, ALURO], [
        ("Digital sensor barriers", "barrier", []),
        ("Phosphor plate barrier envelopes", "envelope", []),
        ("Bite blocks for x-ray holders", "block", []),
    ]),
    "Equipment care": ([HENRY_SCHEIN, INDEPENDENT], [
        ("Handpiece lubricant spray", "can", []),
        ("Suction line cleaner", "bottle", [ALURO]),
        ("Waterline cleaner", "bottle", []),
    ]),
}
