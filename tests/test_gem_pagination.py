import unittest

from keyboards import GEM_PRODUCTS_PER_PAGE, gems_list_keyboard


def gem_row(index, catalogue_name=None):
    return (
        index, f'بسته {index}', index, 0, index * 1000, None, 'once',
        'by_id', True, catalogue_name or str(index), 9999, True,
    )


class GemPaginationTests(unittest.TestCase):
    def test_diamonds_and_memberships_precede_level_up_packages(self):
        gems = [
            gem_row(1, 'Level Up Package - Level 6'),
            gem_row(2, 'Level Up Package - Level 10'),
            gem_row(3, 'Level Up Package - Level 15'),
            gem_row(4, 'Level Up Package - Level 20'),
            gem_row(5, 'Level Up Package - Level 25'),
            gem_row(6, 'Level Up Package - Level 30'),
            gem_row(7, '110'),
            gem_row(8, '231'),
            gem_row(9, 'Weekly Membership'),
            gem_row(10, 'Booyah Pass'),
            gem_row(11, '583'),
            gem_row(12, '1188'),
            gem_row(13, 'Monthly Membership'),
            gem_row(14, '2420'),
        ]
        first = gems_list_keyboard(gems, page=1).inline_keyboard
        second = gems_list_keyboard(gems, page=2).inline_keyboard

        self.assertEqual(
            [row[0].callback_data for row in first[:GEM_PRODUCTS_PER_PAGE]],
            [f'gem_{i}' for i in range(7, 15)],
        )
        self.assertEqual(
            [row[0].callback_data for row in second[:6]],
            [f'gem_{i}' for i in range(1, 7)],
        )
        self.assertEqual(first[8][-1].callback_data, 'gems_page_2')
        self.assertEqual(second[6][0].callback_data, 'gems_page_1')

    def test_requested_page_is_clamped(self):
        gems = [gem_row(i) for i in range(1, 4)]
        rows = gems_list_keyboard(gems, page=99).inline_keyboard
        self.assertEqual(rows[0][0].callback_data, 'gem_1')
