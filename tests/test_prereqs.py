import unittest

from services.prereqs import PrereqSyntaxError, evaluate, parse


class TestPrereqsGrammar(unittest.TestCase):
    def test_single_course(self):
        expr = parse("MATH101")
        self.assertTrue(evaluate(expr, {"MATH101"}))
        self.assertFalse(evaluate(expr, {"PHYS101"}))

    def test_and_requires_all(self):
        expr = parse("PHYS101 AND CHEM101")
        self.assertFalse(evaluate(expr, {"PHYS101"}))
        self.assertTrue(evaluate(expr, {"PHYS101", "CHEM101"}))

    def test_or_requires_any(self):
        expr = parse("ECON101 OR ECON102")
        self.assertTrue(evaluate(expr, {"ECON102"}))
        self.assertFalse(evaluate(expr, {"MATH101"}))

    def test_parentheses_grouping(self):
        expr = parse("(MATH101 AND PHYS101) OR CHEM101")
        self.assertTrue(evaluate(expr, {"CHEM101"}))
        self.assertTrue(evaluate(expr, {"MATH101", "PHYS101"}))
        self.assertFalse(evaluate(expr, {"MATH101"}))

    def test_and_precedence_over_or(self):
        # AND binds tighter: MATH101 OR (PHYS101 AND CHEM101)
        expr = parse("MATH101 OR PHYS101 AND CHEM101")
        self.assertTrue(evaluate(expr, {"MATH101"}))
        self.assertFalse(evaluate(expr, {"PHYS101"}))
        self.assertTrue(evaluate(expr, {"PHYS101", "CHEM101"}))

    def test_case_insensitive_operators(self):
        expr = parse("math101 aNd phys101")
        self.assertTrue(evaluate(expr, {"MATH101", "PHYS101"}))

    def test_reject_commas(self):
        with self.assertRaises(PrereqSyntaxError):
            parse("MATH101, PHYS101")

    def test_reject_missing_operator(self):
        with self.assertRaises(PrereqSyntaxError):
            parse("MATH101 PHYS101")

    def test_reject_empty_parentheses(self):
        with self.assertRaises(PrereqSyntaxError):
            parse("()")


if __name__ == "__main__":
    unittest.main()
