"""Check source-worktree immutability and exact method-file migration."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source, destination):
    baseline=json.loads((destination/'docs/migration/source-manifest.json').read_text())
    source_changes=[];method_changes=[];methods=0
    for row in baseline['files']:
        path=source/row['path']
        if not path.is_file() or sha(path)!=row['sha256']:source_changes.append(row['path'])
        if row['path'].startswith('Quality_Assessment/algorithms/'):
            methods+=1;new=destination/row['path']
            if not new.is_file() or sha(new)!=row['sha256']:method_changes.append(row['path'])
    return {'schema_version':'edc_migration_verification_v1','baseline_source_head':baseline['source_head'],
        'selected_source_files':len(baseline['files']),'source_snapshot_changes':source_changes,
        'method_files_checked':methods,'method_changes':method_changes,
        'passed':not source_changes and not method_changes}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=verify(args.source,Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
