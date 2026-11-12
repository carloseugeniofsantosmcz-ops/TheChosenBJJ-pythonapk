import ast
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / 'main.py').read_text()

class TestSecurity(unittest.TestCase):
    def test_no_follow_redirects_on_api(self):
        self.assertIn('allow_redirects=False', SOURCE)
    def test_sensitive_health_fields_not_requested_in_student_listing(self):
        self.assertNotIn("'id,name,cpf,birth,group_name,monthly,emergency,health_issue,health_note,guardian'", SOURCE)
    def test_attendance_rejects_stale_context(self):
        self.assertIn('requested_date = date.text.strip()', SOURCE)
        self.assertIn('requested_group = group.text.strip()', SOURCE)
    def test_parse(self):
        ast.parse(SOURCE)
