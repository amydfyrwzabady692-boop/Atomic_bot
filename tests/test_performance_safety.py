import asyncio
import inspect
import unittest
from pathlib import Path

import bot
import db
from admin_notify import is_admin, is_premium_admin
from handlers import gems, payment


class _Identity:
    def __init__(self, value):
        self.id = value


class _Update:
    def __init__(self, user_id):
        self.effective_user = _Identity(user_id)
        self.effective_chat = _Identity(user_id)


class PerUserConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_updates_stay_serial(self):
        processor = bot.PerUserUpdateProcessor(8)
        events = []

        async def work(label):
            events.append(f"start:{label}")
            await asyncio.sleep(0.01)
            events.append(f"end:{label}")

        await asyncio.gather(
            processor.do_process_update(_Update(1), work("one")),
            processor.do_process_update(_Update(1), work("two")),
        )
        self.assertEqual(
            events,
            ["start:one", "end:one", "start:two", "end:two"],
        )

    async def test_different_users_can_progress_concurrently(self):
        processor = bot.PerUserUpdateProcessor(8)
        active = 0
        peak = 0

        async def work():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

        await asyncio.gather(
            processor.do_process_update(_Update(1), work()),
            processor.do_process_update(_Update(2), work()),
        )
        self.assertEqual(peak, 2)


class PerformanceGuardTests(unittest.TestCase):
    def test_invalid_role_identity_is_denied_without_crashing(self):
        self.assertFalse(is_admin(None))
        self.assertFalse(is_admin("not-a-user"))
        self.assertFalse(is_premium_admin(None))

    def test_database_uses_bounded_connection_pool(self):
        source = inspect.getsource(db.open_db_pool)
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        self.assertIn("ConnectionPool", source)
        self.assertIn("max_size=max_size", source)
        self.assertIn("psycopg[binary,pool]", requirements)

    def test_slow_supplier_work_is_offloaded_from_telegram_loop(self):
        gem_source = inspect.getsource(gems.gem_get_uid)
        availability_source = inspect.getsource(gems._gem_api_availability)
        payment_source = Path("handlers/payment.py").read_text(encoding="utf-8")
        self.assertIn("asyncio.to_thread(g2bulk.check_player_id", gem_source)
        self.assertIn("await asyncio.to_thread", availability_source)
        self.assertNotIn("= fulfill_order(order_id)", payment_source)
        self.assertIn(
            "await fulfill_order_async(order_id)",
            payment_source,
        )
        helper_source = inspect.getsource(payment.fulfill_order_async)
        self.assertIn("_FULFILLMENT_SLOTS", helper_source)
        self.assertIn("asyncio.to_thread(fulfill_order", helper_source)


if __name__ == "__main__":
    unittest.main()
