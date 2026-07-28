import inspect
import unittest

import db
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
