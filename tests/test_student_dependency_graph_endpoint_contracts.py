import unittest
from pathlib import Path


class TestStudentDependencyGraphEndpointContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.student_routes = (root / 'Routes' / 'student_routes.py').read_text(encoding='utf-8')
        cls.app_py = (root / 'app.py').read_text(encoding='utf-8')

    def test_student_blueprint_endpoint_exists(self):
        content = self.student_routes
        self.assertIn("@student_bp.route('/api/course-details/<int:course_id>', methods=['GET'])", content)
        self.assertIn('def api_course_details_graph():', content)
        self.assertIn('dependency_graph_for_course(', content)

    def test_response_includes_required_graph_sets(self):
        content = self.student_routes
        self.assertIn("'direct_requirements': dependency_graph.get('direct_requirements', [])", content)
        self.assertIn("'dependent_unlockables': dependency_graph.get('dependent_unlockables', [])[:500]", content)
        self.assertIn("'conditional_paths': dependency_graph.get('conditional_paths', [])[:500]", content)

    def test_exact_alias_route_is_registered(self):
        content = self.app_py
        self.assertIn("'/api/student/course-details/<int:course_id>'", content)
        self.assertIn("view_func=app.view_functions['student.api_course_details_graph']", content)


if __name__ == '__main__':
    unittest.main()
