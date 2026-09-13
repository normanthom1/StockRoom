"""The capture endpoints as the service worker's offline queue uses them.

A live tap sends a client_id; a replayed tap sends the same client_id plus
occurred_at. See docs/offline-testing.md for the by-hand airplane-mode check.
"""

import uuid
from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .forecast import forecast
from .models import Item, StockEvent, Supplier


class OfflineSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.other = User.objects.create_user("johanna@example.com", "pw", organisation=cls.org)
        supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=supplier)
        cls.url = f"/log-usage/{cls.item.pk}/used-one/"

    def setUp(self):
        self.client.force_login(self.assistant)

    def replay(self, occurred_at, client_id=None, url=None, **extra):
        data = {"client_id": str(client_id or uuid.uuid4()), "user_id": str(self.assistant.pk),
                "occurred_at": occurred_at.isoformat(), **extra}
        return self.client.post(url or self.url, data)

    # Idempotency

    def test_live_tap_records_its_client_id(self):
        client_id = uuid.uuid4()
        self.client.post(self.url, {"client_id": str(client_id), "user_id": str(self.assistant.pk)})
        self.assertEqual(StockEvent.objects.get().client_id, client_id)

    def test_the_same_tap_sent_twice_is_logged_once(self):
        client_id = str(uuid.uuid4())
        self.client.post(self.url, {"client_id": client_id})
        second = self.client.post(self.url, {"client_id": client_id})
        self.assertEqual(StockEvent.objects.count(), 1)
        self.assertEqual(second["HX-Refresh"], "true")
        # Still just the first tap's undo toast (unread, so it's carried over); the second added none.
        self.assertEqual(len(list(get_messages(second.wsgi_request))), 1)

    def test_replay_of_a_tap_that_already_reached_the_server_is_not_a_duplicate(self):
        # The live request got through but its reply was lost, so the device queued it.
        client_id = uuid.uuid4()
        self.client.post(self.url, {"client_id": str(client_id)})
        response = self.replay(timezone.now() - timedelta(minutes=1), client_id=client_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockEvent.objects.count(), 1)

    def test_five_queued_taps_replayed_twice_each_log_exactly_five_events(self):
        taps = [(uuid.uuid4(), timezone.now() - timedelta(minutes=n)) for n in range(5)]
        for _ in range(2):
            for client_id, when in taps:
                self.assertEqual(self.replay(when, client_id=client_id).status_code, 200)
        self.assertEqual(StockEvent.objects.filter(kind="used").count(), 5)

    def test_bad_client_id_is_rejected(self):
        response = self.client.post(self.url, {"client_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StockEvent.objects.exists())

    def test_tap_without_a_client_id_still_logs(self):
        self.client.post(self.url)
        self.assertIsNone(StockEvent.objects.get().client_id)

    # Replay responses

    def test_replay_uses_the_taps_own_time(self):
        when = timezone.now() - timedelta(hours=3)
        self.replay(when)
        self.assertEqual(StockEvent.objects.get().created_at, when)

    def test_replay_gets_a_plain_ok_with_no_redirect_or_undo_toast(self):
        response = self.replay(timezone.now() - timedelta(hours=3))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Refresh", response)
        self.assertEqual(len(list(get_messages(response.wsgi_request))), 0)

    def test_replay_works_for_every_capture_kind(self):
        when = timezone.now() - timedelta(hours=1)
        self.replay(when, url=f"/log-usage/{self.item.pk}/running-low/")
        self.replay(when, url=f"/log-usage/{self.item.pk}/used-last/")
        self.assertEqual(sorted(StockEvent.objects.values_list("kind", flat=True)), ["low", "out"])

    # Timestamp clamping

    def test_time_a_little_ahead_of_the_server_is_clamped_to_now(self):
        before = timezone.now()
        self.assertEqual(self.replay(before + timedelta(minutes=2)).status_code, 200)
        created_at = StockEvent.objects.get().created_at
        self.assertGreaterEqual(created_at, before)
        self.assertLessEqual(created_at, timezone.now())

    def test_time_clearly_in_the_future_is_rejected(self):
        self.assertEqual(self.replay(timezone.now() + timedelta(minutes=10)).status_code, 400)
        self.assertFalse(StockEvent.objects.exists())

    def test_time_more_than_seven_days_old_is_rejected(self):
        self.assertEqual(self.replay(timezone.now() - timedelta(days=7, minutes=1)).status_code, 400)
        self.assertFalse(StockEvent.objects.exists())

    def test_time_just_under_seven_days_old_is_accepted(self):
        self.assertEqual(self.replay(timezone.now() - timedelta(days=6, hours=23)).status_code, 200)

    def test_unreadable_or_timezone_less_time_is_rejected(self):
        for raw in ["yesterday", "2026-09-12T10:00:00"]:
            response = self.client.post(self.url, {"client_id": str(uuid.uuid4()), "occurred_at": raw})
            self.assertEqual(response.status_code, 400, raw)
        self.assertFalse(StockEvent.objects.exists())

    # Shared devices

    def test_tap_queued_by_someone_else_is_refused_and_left_for_them(self):
        response = self.replay(timezone.now() - timedelta(hours=1), user_id=str(self.other.pk))
        self.assertEqual(response.status_code, 409)
        self.assertFalse(StockEvent.objects.exists())

    # Conflicts

    def test_late_synced_old_stocktake_does_not_override_a_newer_count(self):
        now = timezone.now()
        StockEvent.objects.create(organisation=self.org, item=self.item, user=self.other, kind="count", qty=10,
                                  created_at=now - timedelta(days=1))
        # "Used the last one" from two days ago only reaches the server now.
        self.replay(now - timedelta(days=2), url=f"/log-usage/{self.item.pk}/used-last/")
        f = forecast(StockEvent.objects.filter(item=self.item), [], lead_days=5, now=now)
        self.assertEqual(f.on_hand, 10)
