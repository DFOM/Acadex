import unittest

from services.prereqs import PrereqSyntaxError, dependency_graph_for_course, evaluate, parse


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


class TestDependencyGraphSets(unittest.TestCase):
    def test_dependency_graph_returns_three_deterministic_sets(self):
        catalog = [
            {
                'id': 101,
                'Course_Number': 'CS301',
                'Course_Title': 'Advanced Computing',
                'Day_and_Period': 'Mon 3',
                'Prerequisite': 'MATH101 OR PHYS101',
            },
            {
                'id': 102,
                'Course_Number': 'BIO200',
                'Course_Title': 'Bio Systems',
                'Day_and_Period': 'Tue 2',
                'Prerequisite': '(MATH101 OR STAT100) AND CHEM101',
            },
            {
                'id': 103,
                'Course_Number': 'PHYS201',
                'Course_Title': 'Applied Physics',
                'Day_and_Period': 'Wed 1',
                'Prerequisite': 'MATH101 AND CHEM101',
            },
        ]

        graph = dependency_graph_for_course(
            course_number='MATH101',
            course_title='Calculus I',
            prerequisite_text='CHEM100 OR PHYS100',
            catalog_courses=catalog,
            completed_courses={'CHEM100'},
        )

        direct = graph.get('direct_requirements', [])
        unlocks = graph.get('dependent_unlockables', [])
        paths = graph.get('conditional_paths', [])

        self.assertEqual(2, len(direct))
        self.assertEqual(3, len(unlocks))
        self.assertEqual(2, len(paths))

        direct_tokens = [item.get('course_token') for item in direct]
        self.assertEqual(['CHEM100', 'PHYS100'], direct_tokens)
        self.assertTrue(graph.get('direct_requirements_eligible_now'))

        unlock_numbers = [item.get('course_number') for item in unlocks]
        self.assertEqual(['BIO200', 'CS301', 'PHYS201'], unlock_numbers)

        conditional_unlock_numbers = [item.get('unlock_course_number') for item in paths]
        self.assertEqual(['BIO200', 'CS301'], conditional_unlock_numbers)

    def test_dependency_graph_reports_parse_errors_without_crashing(self):
        catalog = [
            {
                'Course_Number': 'BAD200',
                'Course_Title': 'Bad Syntax Course',
                'Day_and_Period': 'Thu 2',
                'Prerequisite': 'MATH101, PHYS101',
            }
        ]

        graph = dependency_graph_for_course(
            course_number='MATH101',
            course_title='Calculus I',
            prerequisite_text='CHEM100',
            catalog_courses=catalog,
            completed_courses=set(),
        )

        parse_errors = graph.get('parse_errors', [])
        self.assertEqual(1, len(parse_errors))
        self.assertEqual('dependent_unlockables', parse_errors[0].get('scope'))
        self.assertEqual('BAD200', parse_errors[0].get('course_number'))


if __name__ == "__main__":
    unittest.main()
