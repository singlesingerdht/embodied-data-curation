"""SAM3 worker consumes EDC-prepared RGB images and stable episode mappings."""
import json
from pathlib import Path
import sys

from edc.reporting.results import write_json
from .algorithm import evaluate_prepared
from .metrics import normalize_config


def execute(request):
    params = request['params']
    work = Path(request['workdir'])
    config = normalize_config({**params, 'source': params['channel']})
    result = evaluate_prepared(request['rows'], config, params, request['dataset_id'],
                               work, request['provenance'])
    write_json(work / 'native-result.json', result)
    return result


if __name__ == '__main__':
    execute(json.loads(Path(sys.argv[1]).read_text()))
