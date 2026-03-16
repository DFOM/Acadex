import unittest

from services.conflicts import has_conflict, parse_day_period_blocks


class TestConflicts(unittest.TestCase):
    def test_parse_empty_and_tba(self):
        self.assertEqual(parse_day_period_blocks(None), set())
        self.assertEqual(parse_day_period_blocks(''), set())
        self.assertEqual(parse_day_period_blocks('TBA'), set())

    def test_parse_common_formats(self):
        self.assertEqual(parse_day_period_blocks('Mon 1 Wed 1'), {'MON1', 'WED1'})
        self.assertEqual(parse_day_period_blocks('Mon1,Wed1'), {'MON1', 'WED1'})
        self.assertEqual(parse_day_period_blocks('Tue2 Thu2'), {'TUE2', 'THU2'})

    def test_conflict_detection(self):
        occupied = {'MON1', 'WED1'}
        self.assertTrue(has_conflict(occupied, {'MON1'}))
        self.assertFalse(has_conflict(occupied, {'TUE1'}))


if __name__ == '__main__':
    unittest.main()
