import inspect
import unittest

import db
from handlers import gems, sensitivity


class AtomicCheckoutTests(unittest.TestCase):
    def test_gem_handler_uses_single_atomic_checkout_entrypoint(self):
        source = inspect.getsource(gems.gem_confirm)
        self.assertIn("create_gem_order_atomic", source)
        self.assertNotIn("add_order_item(", source)
        self.assertNotIn("add_gem_order_info(", source)

    def test_sensitivity_handler_uses_single_atomic_checkout_entrypoint(self):
        source = inspect.getsource(sensitivity.sens_buy)
        self.assertIn("create_sense_order_atomic", source)
        self.assertNotIn("add_order_item(", source)

    def test_atomic_helpers_lock_catalogue_and_rollback_on_failure(self):
        gem_source = inspect.getsource(db.create_gem_order_atomic)
        sense_source = inspect.getsource(db.create_sense_order_atomic)
        for source in (gem_source, sense_source):
            self.assertIn("FOR UPDATE", source)
            self.assertIn("conn.rollback()", source)
            self.assertIn("conn.commit()", source)
        self.assertIn("قیمت بسته تغییر کرده", gem_source)

    def test_manual_inventory_is_reserved_then_released_or_consumed_once(self):
        create_source = inspect.getsource(db.create_gem_order_atomic)
        release_source = inspect.getsource(db._release_manual_gem_reservations)
        fulfill_source = inspect.getsource(db._reserve_manual_gem)
        cancel_source = inspect.getsource(db.cancel_order_and_refund)
        expire_source = inspect.getsource(db.expire_order_and_refund)
        self.assertIn("MANUAL_RESERVED", create_source)
        self.assertIn('"Stock"="Stock"-1', create_source)
        self.assertIn('"Stock"="Stock"+%s', release_source)
        self.assertIn("MANUAL_RELEASED", release_source)
        self.assertIn("MANUAL_RESERVED", fulfill_source)
        self.assertIn("_release_manual_gem_reservations", cancel_source)
        self.assertIn("_release_manual_gem_reservations", expire_source)
        self.assertIn("_park_zarinpal_authority_for_order", expire_source)

    def test_catalogue_sort_and_order_history_are_durable(self):
        source = inspect.getsource(db.ensure_admin_schema)
        gem_list = inspect.getsource(db.get_gems_by_id)
        sense_list = inspect.getsource(db.list_sense_packages)
        move_source = inspect.getsource(db.move_catalogue_item)
        self.assertIn('"OrderStatusHistory"', source)
        self.assertIn("trg_atomic_order_status_transition", source)
        self.assertIn('"SortOrder"', gem_list)
        self.assertIn('"SortOrder"', sense_list)
        self.assertIn("executemany", move_source)

    def test_financial_health_reconciles_wallet_against_paid_ledger(self):
        source = inspect.getsource(db.financial_health_snapshot)
        self.assertIn("wallet_mismatches", source)
        self.assertIn('COALESCE(t."IsPaid",false)=true', source)
        self.assertIn("WHEN t.\"Kind\"=\\'spend\\' THEN -t.\"Amount\"", source)

    def test_legacy_non_atomic_wallet_refund_is_disabled(self):
        with self.assertRaises(RuntimeError):
            db.refund_order_wallet(1)
        self.assertFalse(hasattr(db, "set_order_payment_method"))
        self.assertFalse(hasattr(db, "set_order_wallet_paid"))


if __name__ == "__main__":
    unittest.main()
