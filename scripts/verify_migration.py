"""Verify the source snapshot and method logic after the package relocation."""
import argparse
import ast
import hashlib
import json
from pathlib import Path


MODULES = {
    'Quality_Assessment': 'edc.algorithms.quality',
    'framework.ir': 'edc.data.ir', 'framework.readers': 'edc.data.readers',
    'framework.transforms': 'edc.data.transforms', 'framework.conversion': 'edc.data.conversion',
    'framework.robots': 'edc.robots', 'framework.channel_names': 'edc.algorithms.channel_names',
    'framework.compat': 'edc.data.compat',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def method_ast(text):
    # Only known import/module prefixes are normalized; constants, calculations,
    # thresholds, control flow and function signatures remain part of the AST.
    for before, after in sorted(MODULES.items(), key=lambda item: -len(item[0])):
        text = text.replace(before, after)
    return ast.dump(ast.parse(text.lstrip('\ufeff')), include_attributes=False)


def verify(source, destination):
    baseline = json.loads((destination/'docs/migration/source-manifest.json').read_text())
    source_changes, method_changes, files = [], [], []
    for row in baseline['files']:
        original = source/row['path']
        if not original.is_file() or sha(original) != row['sha256']:
            source_changes.append(row['path'])
        if not row['path'].startswith('Quality_Assessment/algorithms/'):
            continue
        relative = Path(row['path']).relative_to('Quality_Assessment')
        migrated = destination/'src/edc/algorithms/quality'/relative
        if original.suffix != '.py':
            continue
        same = (original.is_file() and migrated.is_file() and
                method_ast(original.read_text()) == method_ast(migrated.read_text()))
        files.append({'source': row['path'], 'destination': str(migrated.relative_to(destination)),
                      'module_normalized_ast_equal': same})
        if not same:
            method_changes.append(row['path'])
    return {'schema_version': 'edc_migration_verification_v1',
        'baseline_source_head': baseline['source_head'], 'selected_source_files': len(baseline['files']),
        'source_snapshot_changes': source_changes, 'method_files_checked': len(files),
        'method_changes': method_changes, 'method_evidence': files,
        'comparison': 'Python AST after known import/module path relocation only',
        'passed': bool(files) and not source_changes and not method_changes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.source, Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'method_evidence'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
