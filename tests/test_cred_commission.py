"""سهم ۴۰٪ سود بسته برای ادمین جم با اطلاعات."""
import unittest
from decimal import Decimal
from unittest.mock import patch

import profitability


class CommissionMathTests(unittest.TestCase):
    def test_share_is_forty_percent_of_gross_profit_not_sale(self):
        sale = 930_000
        cost_usd = Decimal('6.64')
        rate = 100_000
        cost_toman, gross = profitability.calculate_gross_profit(sale, cost_usd, rate)
        self.assertEqual(cost_toman, 664_000)
        self.assertEqual(gross, 266_000)
        share = gross * 40 // 100
        self.assertEqual(share, 106_400)
        self.assertNotEqual(share, sale * 40 // 100)

    def test_default_share_setting(self):
        import db
        with patch.object(db, 'get_setting', return_value='40'):
            self.assertEqual(db.credential_admin_share_percent(), 40)
        with patch.object(db, 'get_setting', return_value='35'):
            self.assertEqual(db.credential_admin_share_percent(), 35)


class CredCommissionHandlerWiringTests(unittest.TestCase):
    def test_keyboard_and_router_expose_ledger(self):
        import keyboards
        from handlers.cred_commission import cred_commission_handlers
        from handlers.admin_extended import _credential_staff_allowed

        labels = [
            btn.text
            for row in keyboards.credential_admin_home_keyboard().inline_keyboard
            for btn in row
        ]
        self.assertTrue(any('طلب' in t or 'تسویه' in t for t in labels))
        self.assertTrue(_credential_staff_allowed('admx_credpay'))
        self.assertTrue(_credential_staff_allowed('admx_credpay_list_owed'))
        handlers = cred_commission_handlers()
        self.assertEqual(len(handlers), 1)


if __name__ == '__main__':
    unittest.main()
