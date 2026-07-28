import inspect
import unittest

import db
import bot
from handlers import payment


class FulfillmentSubmissionSafetyTests(unittest.TestCase):
    def test_ambiguous_or_legacy_failed_order_is_reconciled_not_resubmitted(self):
        source = inspect.getsource(db.fulfill_order)
        reconcile_at = source.index("g2_status in ('SUBMITTING', 'SUBMIT_UNKNOWN', 'FAILED')")
        submit_at = source.index('g2bulk.place_game_order')
        self.assertLess(reconcile_at, submit_at)
        self.assertIn('find_game_order_by_remark', source)
        self.assertIn("status='SUBMIT_UNKNOWN'", source)

    def test_submission_is_claimed_before_provider_call(self):
        source = inspect.getsource(db.fulfill_order)
        self.assertLess(
            source.index('claim_gem_submission'),
            source.index('g2bulk.place_game_order'),
        )

    def test_processing_orders_are_polled_but_not_listed_as_failed(self):
        processing_source = inspect.getsource(db.list_processing_auto_orders)
        failed_source = inspect.getsource(db.list_failed_deliveries)
        self.assertIn("processing", processing_source)
        self.assertIn("SUBMIT_UNKNOWN", processing_source)
        self.assertNotIn('"Status"=\\\'processing\\\'', failed_source)

    def test_processing_is_not_reported_as_a_fulfillment_failure(self):
        alert_source = inspect.getsource(payment._alert_fulfill_issue)
        approve_source = inspect.getsource(payment.admin_approve)
        preflight_source = inspect.getsource(payment._delivery_preflight)
        self.assertIn("if status == 'processing':", alert_source)
        self.assertIn("elif success and status == 'processing':", approve_source)
        self.assertIn('_g2_status', preflight_source)

    def test_successful_background_reconciliation_notifies_user_and_admin(self):
        source = inspect.getsource(bot._g2_reconcile_loop)
        self.assertIn('app.bot.send_message', source)
        self.assertIn('notify_admin', source)
        self.assertIn('list_unnotified_auto_deliveries', source)
        self.assertIn('mark_delivery_notified', source)

    def test_delivery_notification_outbox_is_scoped_to_completed_supplier_orders(self):
        source = inspect.getsource(db.list_unnotified_auto_deliveries)
        self.assertIn('"G2BulkStatus"=\\\'COMPLETED\\\'', source)
        self.assertIn('"PaymentVerifiedAt" IS NOT NULL', source)
        marker_source = inspect.getsource(db.mark_delivery_notified)
        self.assertIn("Status", marker_source)

    def test_authenticated_webhook_never_submits_and_requires_exact_paid_link(self):
        source = inspect.getsource(db.apply_g2bulk_webhook)
        self.assertNotIn('place_game_order', source)
        self.assertIn('provider order id mismatch', source)
        self.assertIn('player id mismatch', source)
        self.assertIn('payment is not verified', source)
        self.assertIn("existing_g2_status", source)

    def test_manual_reconciliation_is_read_only_before_exact_paid_row_update(self):
        source = inspect.getsource(db.reconcile_completed_g2_order)
        self.assertIn('get_game_order_status', source)
        self.assertIn('"G2BulkOrderId"=%s AND g."GameUID"=%s', source)
        self.assertIn('get_game_order_details', source)
        self.assertIn("details.get('status') != 'COMPLETED'", source)
        self.assertIn('provider_player_id != expected_player_id', source)
        self.assertIn('"PaymentVerifiedAt" IS NOT NULL', source)
        self.assertIn('len(selected) != 1', source)
        self.assertNotIn('place_game_order', source)
