"""Create a four-component, single-arm synthetic curation demonstration."""
import argparse
import importlib.util
from pathlib import Path
import yaml


def generate(output):
    source = Path(__file__).resolve().parents[1] / 'workflows/make_example.py'
    spec = importlib.util.spec_from_file_location('stage_example', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.generate(output)
    root = original.parent
    workflow = yaml.safe_load(original.read_text())
    workflow.pop('schema_version')
    workflow.pop('dataset')
    # This example explicitly opts into SDK methods, which automatic pools exclude.
    workflow['stages'][0]['algorithms'] = ['Structure_Check', 'team_example__finite_check']
    profile = yaml.safe_load((root/'source/robot.yaml').read_text())
    profile['parts'] = {'main': {'type': 'arm', 'side': 'unknown', 'dof': 2}}
    profile['channels']['joint_pos'].update(robot_part='main', side='unknown', logical_name='joint/arm/main/feedback')
    folder = root / 'config'
    folder.mkdir()
    components = {
        'robot': profile,
        'dataset': {'path': '../source', 'id': 'synthetic_drawer', 'tasks': ['synthetic drawer; no real demonstration']},
        'algorithms': {'defaults': {'arms': 'all', 'selection': 'explicit'}, 'methods': {}},
        'workflow': workflow}
    for name, value in components.items():
        (folder/f'{name}.yaml').write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
    target = root/'curation.yaml'
    target.write_text(yaml.safe_dump({'schema_version': 'edc_config_v1', **{
        key: {'ref': f'config/{key}.yaml'} for key in components}},sort_keys=False))
    return target


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    print(generate(parser.parse_args().output))
