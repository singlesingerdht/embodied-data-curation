"""Print actual per-episode recommendations; does not execute algorithms."""
import argparse
import json
from pathlib import Path

import edc
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data')
    parser.add_argument('--robot', required=True)
    args = parser.parse_args()
    robot = Path(args.robot).resolve()
    config = yaml.safe_load(robot.read_text(encoding='utf-8'))
    if 'robot' in config and 'schema_version' not in config:
        profile, params, _ = edc.compile_card(config, base=robot.parent)
    else:
        profile, params = config, {}
    with edc.open_dataset(args.data, profile=profile) as dataset:
        report = edc.recommend(dataset, params=params)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
