from django.test import SimpleTestCase

from .mailto import build_mailto_link


class BuildMailtoLinkTests(SimpleTestCase):
    def test_encodes_subject_and_body(self):
        link = build_mailto_link("liz@example.com", "Join StockRoom", "Hi Liz,\n\nHere's your link.")
        self.assertTrue(link.startswith("mailto:liz@example.com?subject="))
        self.assertIn("subject=Join%20StockRoom", link)
        self.assertIn("body=Hi%20Liz%2C%0A%0AHere%27s%20your%20link.", link)

    def test_keeps_the_at_sign_readable_but_encodes_other_specials(self):
        # + is a valid local-part character but means space to some mailto
        # parsers unless percent-encoded; @ is fine unencoded (RFC 6068).
        link = build_mailto_link("liz+dental@example.com", "s", "b")
        self.assertTrue(link.startswith("mailto:liz%2Bdental@example.com?"))
