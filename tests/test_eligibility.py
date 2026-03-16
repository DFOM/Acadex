import unittest

from services.eligibility import normalize_course_code_for_prereqs, prereqs_met


class TestEligibility(unittest.TestCase):
    def test_normalize_strips_section_semester_version(self):
        self.assertEqual(normalize_course_code_for_prereqs('MATH101-S1-Sp'), 'MATH101')
        self.assertEqual(normalize_course_code_for_prereqs('MATH101-S1-Sp-V2'), 'MATH101')
        self.assertEqual(normalize_course_code_for_prereqs('econ-101'), 'ECON101')

    def test_prereqs_met_and_only(self):
        met, err = prereqs_met('PHYS101 AND CHEM101', ['PHYS101'])
        self.assertFalse(met)
        self.assertIsNone(err)

        met, err = prereqs_met('PHYS101 AND CHEM101', ['PHYS101', 'CHEM101'])
        self.assertTrue(met)
        self.assertIsNone(err)

    def test_prereqs_met_or_only(self):
        met, err = prereqs_met('ECON101 OR ECON102', ['ECON102'])
        self.assertTrue(met)
        self.assertIsNone(err)

    def test_prereqs_met_nested_parentheses(self):
        met, err = prereqs_met('(MATH101 AND PHYS101) OR CHEM101', ['CHEM101'])
        self.assertTrue(met)
        self.assertIsNone(err)

        met, err = prereqs_met('(MATH101 AND PHYS101) OR CHEM101', ['MATH101'])
        self.assertFalse(met)
        self.assertIsNone(err)

    def test_invalid_syntax_returns_friendly_error(self):
        met, err = prereqs_met('MATH101, PHYS101', ['MATH101'])
        self.assertFalse(met)
        self.assertEqual(err, 'Invalid prerequisite format')

    def test_none_sentinel_is_allowed(self):
        met, err = prereqs_met('None', [])
        self.assertTrue(met)
        self.assertIsNone(err)


if __name__ == '__main__':
    unittest.main()
