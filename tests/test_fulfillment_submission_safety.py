import inspect
import unittest

import db


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

