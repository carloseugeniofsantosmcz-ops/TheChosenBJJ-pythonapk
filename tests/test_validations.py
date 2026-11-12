import ast
import re
import unittest
from pathlib import Path

# Testa validação de CPF sem importar Kivy (não instalado no runner de testes).
source = Path(__file__).resolve().parents[1].joinpath('main.py').read_text()
tree = ast.parse(source)
cpf = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'valid_cpf')
module = ast.Module(body=[cpf], type_ignores=[])
namespace = {'re': re}
exec(compile(module, '<valid_cpf>', 'exec'), namespace)

class TestCPF(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(namespace['valid_cpf']('529.982.247-25'))
    def test_invalid(self):
        for value in ('', '11111111111', '12345678900', '52998224726', '123'):
            with self.subTest(value=value):
                self.assertFalse(namespace['valid_cpf'](value))

if __name__ == '__main__':
    unittest.main()
