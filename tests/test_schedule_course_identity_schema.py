import re
import unittest
from pathlib import Path


class TestScheduleCourseIdentitySchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.models_text = (root / 'models.py').read_text(encoding='utf-8')

    def test_identity_columns_exist_with_expected_types(self):
        required_patterns = [
            r"course_number\s*=\s*db\.Column\(db\.String\(100\),\s*nullable=False\)",
            r"day_period\s*=\s*db\.Column\(db\.String\(255\),\s*nullable=False\)",
            r"semester\s*=\s*db\.Column\(db\.String\(50\),\s*nullable=False\)",
            r"year\s*=\s*db\.Column\(db\.String\(20\),\s*nullable=False\)",
            r"dataset_name\s*=\s*db\.Column\(db\.String\(150\),\s*nullable=True\)",
            r"source\s*=\s*db\.Column\(db\.String\(20\),\s*nullable=True\)",
        ]
        for pattern in required_patterns:
            self.assertRegex(self.models_text, pattern)

    def test_identity_indexes_exist(self):
        self.assertIn("'ix_schedule_courses_course_term'", self.models_text)
        self.assertIn("'ix_schedule_courses_identity'", self.models_text)
        self.assertRegex(
            self.models_text,
            r"'ix_schedule_courses_course_term'[\s\S]*'course_number'[\s\S]*'semester'[\s\S]*'year'",
        )
        self.assertRegex(
            self.models_text,
            r"'ix_schedule_courses_identity'[\s\S]*'course_number'[\s\S]*'day_period'[\s\S]*'semester'[\s\S]*'year'[\s\S]*'dataset_name'",
        )


if __name__ == '__main__':
    unittest.main()
