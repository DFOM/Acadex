import unittest
from pathlib import Path


class TestAdminDatasetAndTBAContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.admin_routes = (root / 'Routes' / 'admin_routes.py').read_text(encoding='utf-8')

    def test_dataset_delete_syncs_registry_before_lookup(self):
        content = self.admin_routes
        self.assertIn('_ensure_dataset_registry_synced()', content)
        self.assertIn('def api_delete_dataset(dataset_id: int):', content)

    def test_dataset_delete_partial_cleanup_returns_success_payload(self):
        content = self.admin_routes
        self.assertIn("fallback_deleted = False", content)
        self.assertIn("'partial_cleanup': True", content)
        self.assertIn("Dataset registry row was removed, but some physical cleanup steps failed.", content)

    def test_csv_dataset_delete_uses_normalized_legacy_safe_filter(self):
        content = self.admin_routes
        self.assertIn('def _csv_courses_for_dataset_name(dataset_name: str, *, include_legacy_sources: bool = True):', content)
        self.assertIn("source_expr.in_(['csv', ''])", content)
        self.assertIn('sa.func.lower(sa.func.trim(sa.func.coalesce(Course.dataset_name,', content)
        self.assertIn("q = _csv_courses_for_dataset_name(dataset_name, include_legacy_sources=True)", content)

    def test_dataset_delete_exposes_impact_endpoint_and_cleanup(self):
        content = self.admin_routes
        self.assertIn("@admin_bp.route('/api/datasets/<int:dataset_id>/impact', methods=['GET'])", content)
        self.assertIn('def api_dataset_impact(dataset_id: int):', content)
        self.assertIn('def _dataset_dependency_impact(row: DatasetRegistry) -> dict:', content)
        self.assertIn('def _delete_linked_schedules_and_notify(row: DatasetRegistry) -> dict:', content)
        self.assertIn('schedule_cleanup = _delete_linked_schedules_and_notify(row)', content)
        self.assertIn("'schedule_cleanup': schedule_cleanup", content)

    def test_schedule_cleanup_notifies_users_and_deletes_schedule_data(self):
        content = self.admin_routes
        self.assertIn("title='Schedule removed after dataset deletion'", content)
        self.assertIn('ScheduleCourse.query.filter(ScheduleCourse.schedule_id.in_(schedule_ids)).delete', content)
        self.assertIn('AIEvent.query.filter(AIEvent.schedule_id.in_(schedule_ids)).delete', content)
        self.assertIn('BehaviorEvent.query.filter(BehaviorEvent.schedule_id.in_(schedule_ids)).delete', content)
        self.assertIn('Schedule.query.filter(Schedule.id.in_(schedule_ids)).delete', content)

    def test_tba_ingestion_allows_missing_day_period_mapping(self):
        content = self.admin_routes
        self.assertIn("required = {'course_number', 'title', 'faculty'}", content)
        self.assertIn("day_period = str(day_period or '').strip() or 'TBA'", content)

    def test_tba_preview_marks_missing_day_period_as_tba(self):
        content = self.admin_routes
        self.assertIn("if not extracted.get('day_period'):", content)
        self.assertIn("extracted['day_period'] = 'TBA'", content)

    def test_dataset_delete_clears_course_faculty_fk_before_course_delete(self):
        content = self.admin_routes
        self.assertIn('course_faculty.delete().where(course_faculty.c.course_id.in_(course_ids))', content)

    def test_admin_routes_imports_all_required_models(self):
        content = self.admin_routes
        for model in ['ScheduleCourse', 'AIEvent', 'BehaviorEvent', 'course_faculty']:
            self.assertIn(model, content.split('\n')[0:20].__repr__(),
                          f'{model} must be imported at the top of admin_routes.py')

    def test_system_reset_endpoint_exists_and_requires_confirmation(self):
        content = self.admin_routes
        self.assertIn("def api_system_reset():", content)
        self.assertIn("confirm != 'RESET'", content)
        self.assertIn("generate_password_hash", content)

    def test_system_reset_cleans_all_data_tables(self):
        content = self.admin_routes
        for table in ['course_faculty.delete()', 'ScheduleCourse.query.delete',
                       'Course.query.delete', 'Faculty.query.delete',
                       'DatasetRegistry.query.delete', 'User.query.delete',
                       'Notification.query.delete']:
            self.assertIn(table, content, f'System reset must clean {table}')


if __name__ == '__main__':
    unittest.main()
