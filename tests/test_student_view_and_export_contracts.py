import unittest
from pathlib import Path


class TestStudentViewAndExportContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.student_routes = (root / 'Routes' / 'student_routes.py').read_text(encoding='utf-8')
        cls.student_js = (root / 'static' / 'js' / 'student.js').read_text(encoding='utf-8')

    def test_courses_view_has_fail_safe_guard(self):
        content = self.student_routes
        self.assertIn('def courses_view():', content)
        self.assertIn('try:', content)
        self.assertIn("current_app.logger.error('courses_view failed: %s', exc, exc_info=True)", content)
        self.assertIn('filtered_rows = []', content)

    def test_schedule_hydration_sets_identity_fields(self):
        content = self.student_routes
        self.assertIn("payload['identity_day_period']", content)
        self.assertIn("payload['identity_semester']", content)
        self.assertIn("payload['identity_year']", content)
        self.assertIn("payload['identity_dataset_name']", content)

    def test_png_export_forces_high_contrast_clone_styles(self):
        content = self.student_js
        self.assertIn("cloneRoot.querySelectorAll('.ax-course strong')", content)
        self.assertIn("el.style.opacity = '1'", content)
        self.assertIn("el.style.fontWeight = '800'", content)
        self.assertIn("el.style.color = '#ffffff'", content)


if __name__ == '__main__':
    unittest.main()
