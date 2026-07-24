import os
import unittest

from payment_safety import checked_amount, order_amounts, valid_owner


class CheckedAmountTests(unittest.TestCase):
    def test_accepts_integer_and_numeric_string(self):
        self.assertEqual(checked_amount(1), 1)
        self.assertEqual(checked_amount('12500'), 12_500)

    def test_rejects_zero_negative_boolean_and_non_numeric(self):
        for value in (0, -1, True, False, None, 'free'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    checked_amount(value)

    def test_enforces_explicit_bounds(self):
        self.assertEqual(checked_amount(10, minimum=10, maximum=20), 10)
        self.assertEqual(checked_amount(20, minimum=10, maximum=20), 20)
        with self.assertRaises(ValueError):
            checked_amount(9, minimum=10, maximum=20)
        with self.assertRaises(ValueError):
            checked_amount(21, minimum=10, maximum=20)


class OrderAmountTests(unittest.TestCase):
    def test_valid_partial_wallet_payment(self):
        self.assertEqual(
            order_amounts(200_000, discount=20_000, wallet_paid=30_000, item_total=200_000),
            (180_000, 150_000),
        )

    def test_valid_full_wallet_payment(self):
        self.assertEqual(order_amounts(200_000, wallet_paid=200_000), (200_000, 0))

    def test_rejects_free_or_over_discounted_order(self):
        with self.assertRaises(ValueError):
            order_amounts(0)
        with self.assertRaises(ValueError):
            order_amounts(100, discount=100)
        with self.assertRaises(ValueError):
            order_amounts(100, discount=-1)

    def test_rejects_wallet_overpayment_and_item_mismatch(self):
        with self.assertRaises(ValueError):
            order_amounts(100, wallet_paid=101)
        with self.assertRaises(ValueError):
            order_amounts(100, item_total=99)


class OwnershipTests(unittest.TestCase):
    def test_requires_at_least_one_identity(self):
        self.assertFalse(valid_owner(1, '100'))

    def test_requires_every_supplied_identity_to_match(self):
        self.assertTrue(valid_owner(1, '100', user_db_id=1, telegram_id=100))
        self.assertFalse(valid_owner(1, '100', user_db_id=2, telegram_id=100))
        self.assertFalse(valid_owner(1, '100', user_db_id=1, telegram_id=200))


if __name__ == '__main__':
    unittest.main()
