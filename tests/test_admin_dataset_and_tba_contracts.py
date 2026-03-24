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

    def test_tba_ingestion_allows_missing_day_period_mapping(self):
        content = self.admin_routes
        self.assertIn("required = {'course_number', 'title', 'faculty'}", content)
        self.assertIn("day_period = str(day_period or '').strip() or 'TBA'", content)

    def test_tba_preview_marks_missing_day_period_as_tba(self):
        content = self.admin_routes
        self.assertIn("if not extracted.get('day_period'):", content)
        self.assertIn("extracted['day_period'] = 'TBA'", content)


if __name__ == '__main__':
    unittest.main()
