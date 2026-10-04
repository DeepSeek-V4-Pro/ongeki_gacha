"""发布版本号在元数据、配置和用户文档中保持一致。"""
import ast
import json
from pathlib import Path
import re
import tomllib
import unittest

from .. import __version__


class ReleaseMetadataTests(unittest.TestCase):
    def test_documented_configuration_matches_all_defaults(self):
        root = Path(__file__).parents[1]
        usage = (root / 'USAGE.md').read_text(encoding='utf8')
        blocks = [tomllib.loads(block) for block in re.findall(r'```toml\n(.*?)```', usage, re.S)]
        documented = next(block for block in blocks if 'ui' in block)
        tree = ast.parse((root / 'config_model.py').read_text(encoding='utf8'))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

        def literal(node):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                return literal(node.left) * literal(node.right)
            return ast.literal_eval(node)

        for section in classes['OngekiGachaPluginConfig'].body:
            if not isinstance(section, ast.AnnAssign):
                continue
            defaults = {}
            for field in classes[section.annotation.id].body:
                if not isinstance(field, ast.AnnAssign):
                    continue
                for keyword in field.value.keywords:
                    if keyword.arg == 'default':
                        defaults[field.target.id] = literal(keyword.value)
                    elif keyword.arg == 'default_factory':
                        factory = keyword.value
                        defaults[field.target.id] = (literal(factory.body) if isinstance(factory, ast.Lambda)
                                                     else {'list': list, 'dict': dict}[factory.id]())
            self.assertEqual(documented[section.target.id], defaults, section.target.id)
        # The upgrade snippet must recommend the same values as the full reference.
        for block in blocks:
            for section, fields in block.items():
                for name, value in fields.items():
                    self.assertEqual(value, documented[section][name], f'{section}.{name}')

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
