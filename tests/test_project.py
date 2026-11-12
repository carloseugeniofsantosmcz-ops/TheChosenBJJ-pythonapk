import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class TestProject(unittest.TestCase):
    def test_python_parse(self):
        ast.parse((ROOT / 'main.py').read_text())
        ast.parse((ROOT / 'config.py').read_text())
    def test_no_secret_service_role(self):
        cfg = (ROOT / 'config.py').read_text().lower()
        self.assertNotIn('service_role =', cfg)
        self.assertNotIn('sb_secret_', cfg)
    def test_buildozer_configuration(self):
        spec = (ROOT / 'buildozer.spec').read_text()
        self.assertRegex(spec, r'version = 0\.17\.0')
        self.assertIn('android.permissions = INTERNET', spec)
        self.assertIn('requirements = python3,kivy,', spec)

if __name__ == '__main__':
    unittest.main()
