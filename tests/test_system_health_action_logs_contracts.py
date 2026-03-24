import unittest
from pathlib import Path


class TestSystemHealthActionLogsContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.app_py = (root / 'app.py').read_text(encoding='utf-8')
        cls.admin_routes = (root / 'Routes' / 'admin_routes.py').read_text(encoding='utf-8')
        cls.admin_health_tpl = (root / 'templates' / 'Admin' / 'admin_health.html').read_text(encoding='utf-8')

    def test_app_persists_mutation_audit_logs_to_settings(self):
        content = self.app_py
        self.assertIn("_SYSTEM_HEALTH_ACTION_LOGS_KEY = 'system_health_action_logs_json'", content)
        self.assertIn('def _append_system_health_action_log(entry: dict, limit: int = 400) -> None:', content)
        self.assertIn('Setting.query.get(_SYSTEM_HEALTH_ACTION_LOGS_KEY)', content)
        self.assertIn('_append_system_health_action_log(', content)
        self.assertIn("'detail': _safe_log_detail(getattr(g, '_system_action_detail', ''))", content)

    def test_health_metrics_exposes_audit_action_logs(self):
        content = self.admin_routes
        self.assertIn('def _recent_system_action_logs(limit: int = 200) -> list[dict]:', content)
        self.assertIn("row = Setting.query.get('system_health_action_logs_json')", content)
        self.assertIn("'detail': str(item.get('detail') or '')", content)
        self.assertIn("'audit': {", content)
        self.assertIn("'action_log_count': len(action_logs)", content)
        self.assertIn("'action_logs': action_logs", content)

    def test_dataset_routes_emit_health_action_detail(self):
        content = self.admin_routes
        self.assertIn("g._system_action_detail = {'event': 'dataset_delete'", content)
        self.assertIn("g._system_action_detail = {'event': 'dataset_force_delete'", content)
        self.assertIn("g._system_action_detail = {'event': 'dataset_impact'", content)

    def test_health_template_has_copyable_action_logs_panel(self):
        content = self.admin_health_tpl
        self.assertIn('Action Logs', content)
        self.assertIn('id="copyActionLogsBtn"', content)
        self.assertIn('id="actionLogs"', content)
        self.assertIn('function renderActionLogs(auditPayload)', content)
        self.assertIn("detail=${detail}", content)
        self.assertIn('navigator.clipboard.writeText(text)', content)


if __name__ == '__main__':
    unittest.main()
