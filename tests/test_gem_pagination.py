import unittest

from keyboards import gems_list_keyboard


def gem_row(index):
    return (
        index, f'بسته {index}', index, 0, index * 1000, None, 'once',
        'by_id', True, str(index), 9999, True,
    )


class GemPaginationTests(unittest.TestCase):
    def test_fourteen_products_are_split_into_two_pages(self):
        gems = [gem_row(i) for i in range(1, 15)]
        first = gems_list_keyboard(gems, page=1).inline_keyboard
        second = gems_list_keyboard(gems, page=2).inline_keyboard

        self.assertEqual(
            [row[0].callback_data for row in first[:7]],
            [f'gem_{i}' for i in range(1, 8)],
        )
        self.assertEqual(
            [row[0].callback_data for row in second[:7]],
            [f'gem_{i}' for i in range(8, 15)],
        )
        self.assertEqual(first[7][-1].callback_data, 'gems_page_2')
        self.assertEqual(second[7][0].callback_data, 'gems_page_1')

    def test_requested_page_is_clamped(self):
        gems = [gem_row(i) for i in range(1, 4)]
        rows = gems_list_keyboard(gems, page=99).inline_keyboard
        self.assertEqual(rows[0][0].callback_data, 'gem_1')

