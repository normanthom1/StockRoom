"""New Zealand date and number formats.

Django ships no en_NZ locale - only en, en_AU, en_CA, en_GB and en_IE - so
LANGUAGE_CODE = "en-nz" falls back to "en", whose DATE_FORMAT is the American
"N j, Y" ("Sept. 18, 2026"). Most templates pass an explicit format and dodge
it, but anything that doesn't renders month-first, and on an invoice the date
decides which tax year it lands in.

Setting DATE_FORMAT in settings.py does nothing while USE_I18N is on, because
get_format() reads the active locale's format module first and only falls back
to the setting if no module has it. FORMAT_MODULE_PATH in settings.py points
Django here so en_NZ is found before en.
"""

DATE_FORMAT = "j F Y"  # 18 September 2026
SHORT_DATE_FORMAT = "j M Y"  # 18 Sep 2026
DATETIME_FORMAT = "j F Y, g:i a"  # 18 September 2026, 1:30 p.m.
SHORT_DATETIME_FORMAT = "j M Y, g:i a"
YEAR_MONTH_FORMAT = "F Y"
MONTH_DAY_FORMAT = "j F"
TIME_FORMAT = "g:i a"
FIRST_DAY_OF_WEEK = 1  # Monday

# Day first, so a date typed the way a NZ practice writes it validates. The
# ISO form stays accepted because that's what <input type="date"> posts.
DATE_INPUT_FORMATS = [
    "%d/%m/%Y",  # 18/09/2026
    "%d/%m/%y",  # 18/09/26
    "%d-%m-%Y",  # 18-09-2026
    "%Y-%m-%d",  # 2026-09-18, what a date input posts
    "%d %b %Y",  # 18 Sep 2026
    "%d %B %Y",  # 18 September 2026
]
DATETIME_INPUT_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]

DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
