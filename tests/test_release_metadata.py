"""发布版本号在元数据、配置和用户文档中保持一致。"""
import ast
import json
from pathlib import Path
import unittest

from .. import __version__


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_is_consistent(self):
        root = Path(__file__).parents[1]
        manifest = json.loads((root / '_manifest.json').read_text(encoding='utf8'))
        self.assertEqual(__version__, manifest['version'])
        config_tree = ast.parse((root / 'config_model.py').read_text(encoding='utf8'))
        config_version = next(
            keyword.value.value
            for node in ast.walk(config_tree)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == 'config_version'
            for keyword in node.value.keywords
            if keyword.arg == 'default'
        )
        self.assertEqual(__version__, config_version)
        for name in ('README.md', 'USAGE.md'):
            self.assertIn(f'当前版本：{__version__}', (root / name).read_text(encoding='utf8'))
        self.assertIn(f'## {__version__} - ', (root / 'CHANGELOG.md').read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
