import unittest
from pathlib import Path


class TestDynamicMetadataPhase1Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.models = (root / 'models.py').read_text(encoding='utf-8')
        cls.utils = (root / 'utils.py').read_text(encoding='utf-8')
        cls.admin_routes = (root / 'Routes' / 'admin_routes.py').read_text(encoding='utf-8')

    def test_course_and_registry_have_dynamic_schema_columns(self):
        self.assertIn("metadata_json = db.Column(sa.JSON(), nullable=True)", self.models)
        self.assertIn("metadata_schema_json = db.Column(sa.JSON(), nullable=True)", self.models)

    def test_course_metadata_field_model_exists(self):
        self.assertIn("class CourseMetadataField(db.Model):", self.models)
        self.assertIn("__tablename__ = 'course_metadata_fields'", self.models)
        self.assertIn("field_key = db.Column(db.String(120), nullable=False, unique=True, index=True)", self.models)
        self.assertIn("visibility = db.Column(db.String(20), nullable=False, default='student', index=True)", self.models)

    def test_dynamic_ingestion_helpers_exist(self):
        self.assertIn("def dynamic_course_payload_from_row(row: dict[str, Any], mapping: dict[str, list[str]] | None = None) -> dict[str, Any]:", self.utils)
        self.assertIn("def upsert_course_metadata_fields(schema_fields: dict[str, dict[str, str]], *, default_visibility: str = 'student') -> None:", self.utils)
        self.assertIn("def merge_dataset_registry_metadata_schema(dataset_row: DatasetRegistry | None, schema_fields: dict[str, dict[str, str]]) -> None:", self.utils)

    def test_dataset_resolver_accepts_required_metadata_fields(self):
        self.assertIn("required_metadata_fields: list[str] | None = None", self.utils)
        self.assertIn("_registry_row_has_fields", self.utils)

    def test_admin_metadata_visibility_api_exists(self):
        self.assertIn("@admin_bp.route('/api/course-metadata-fields', methods=['GET', 'POST'])", self.admin_routes)
        self.assertIn("def api_course_metadata_fields():", self.admin_routes)
        self.assertIn("@admin_bp.route('/api/course-metadata-fields/<field_key>/visibility', methods=['POST'])", self.admin_routes)
        self.assertIn("def api_course_metadata_field_visibility(field_key: str):", self.admin_routes)

    def test_csv_ingestion_uses_dynamic_mapping(self):
        self.assertIn("dynamic_info = dynamic_course_payload_from_row(raw_row or {}, mapping_normalized)", self.admin_routes)
        self.assertIn("'metadata_json': row_dynamic_metadata", self.admin_routes)
        self.assertIn("upsert_course_metadata_fields(discovered_dynamic_schema, default_visibility='student')", self.admin_routes)


if __name__ == '__main__':
    unittest.main()
