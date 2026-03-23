import unittest
from pathlib import Path


class TestIdentityWritePathContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.student_routes = (root / 'Routes' / 'student_routes.py').read_text(encoding='utf-8')
        cls.student_js = (root / 'static' / 'js' / 'student.js').read_text(encoding='utf-8')

    def test_add_course_persists_identity_fields(self):
        content = self.student_routes
        self.assertIn('day_period=persisted_day_period', content)
        self.assertIn('semester=persisted_semester', content)
        self.assertIn('year=persisted_year', content)
        self.assertIn('dataset_name=persisted_dataset_name or None', content)
        self.assertIn('source=persisted_source or None', content)

    def test_add_course_duplicate_check_uses_identity_context(self):
        content = self.student_routes
        self.assertIn('existing_query = ScheduleCourse.query.filter_by(schedule_id=sched.id, course_number=course_num)', content)
        self.assertIn('func.lower(func.coalesce(ScheduleCourse.day_period,', content)
        self.assertIn('func.lower(func.coalesce(ScheduleCourse.semester,', content)
        self.assertIn('func.lower(func.coalesce(ScheduleCourse.year,', content)
        self.assertIn('func.lower(func.coalesce(ScheduleCourse.dataset_name,', content)

    def test_client_add_payload_includes_identity_fields(self):
        content = self.student_js
        self.assertIn("day_period: decodeDataAttr(btn.getAttribute('data-day-period'))", content)
        self.assertIn("semester: decodeDataAttr(btn.getAttribute('data-semester'))", content)
        self.assertIn("year: decodeDataAttr(btn.getAttribute('data-year'))", content)
        self.assertIn("dataset_name: decodeDataAttr(btn.getAttribute('data-dataset-name'))", content)
        self.assertIn("source: decodeDataAttr(btn.getAttribute('data-source'))", content)

    def test_client_duplicate_detection_uses_instance_identity(self):
        content = self.student_js
        self.assertIn('const instanceIdentityKey = (course) => {', content)
        self.assertIn('currentCourses.some((c) => instanceIdentityKey(c) === instanceIdentityKey(sample))', content)
        self.assertIn('currentCourses.some((c) => instanceIdentityKey(c) === instanceIdentityKey(sec))', content)


if __name__ == '__main__':
    unittest.main()
