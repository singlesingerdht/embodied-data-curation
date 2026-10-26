"""Compare all nine selected CPU adapter reports on the same synthetic MCAP.

Requires formats extras and ffmpeg. No real robot or training claim is made.
Only per-run elapsed-time fields and artifact-root paths are normalized.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

IDS=['Structure_Check','Numeric_Check','Action_Smoothness','Check_Timestamp','Sync_Check',
     'Root_Cause','Dedup_Check','Reset_Detection','Video_Integrity']


def generate(root):
    import cv2
    import numpy as np
    import yaml
    from rosbags.typesys import get_typestore,Stores
    from mcap.writer import Writer
    from framework.robots.numeric_review import confirm_numeric_profile
    source=root/'synthetic.mcap'
    store=get_typestore(Stores.ROS2_HUMBLE)
    joint='sensor_msgs/msg/JointState';pose='geometry_msgs/msg/PoseStamped';image='sensor_msgs/msg/CompressedImage'
    with source.open('wb') as stream:
        writer=Writer(stream);writer.start(profile='ros2')
        channels={}
        for name,typ in [('/right_joint_states',joint),('/right_end_pose',pose),('/head_rgb',image)]:
            msgdef,_=store.generate_msgdef(typ,ros_version=2)
            schema=writer.register_schema(typ,'ros2msg',msgdef.encode())
            channels[name]=writer.register_channel(name,'cdr',schema)
        rng=np.random.default_rng(20261026)
        for name,typ,count,period in [('/right_joint_states',joint,400,.01),('/right_end_pose',pose,400,.01),('/head_rgb',image,120,1/30)]:
            for i in range(count):
                ns=10_000_000_000+round(i*period*1e9)+(1_000_000 if name=='/right_end_pose' else 0)
                stamp=store.types['builtin_interfaces/msg/Time'](ns//10**9,ns%10**9)
                header=store.types['std_msgs/msg/Header'](stamp,'base')
                if typ==joint:
                    msg=store.types[joint](header,['q0','q1'],np.array([.1*np.sin(i/30),0.]),np.zeros(2),np.zeros(2))
                elif typ==pose:
                    point=store.types['geometry_msgs/msg/Point'](0.,0.,.2)
                    quaternion=store.types['geometry_msgs/msg/Quaternion'](0.,0.,0.,1.)
                    msg=store.types[pose](header,store.types['geometry_msgs/msg/Pose'](point,quaternion))
                else:
                    pixels=rng.integers(40,190,(48,64,3),dtype=np.uint8)
                    ok,encoded=cv2.imencode('.jpg',pixels);assert ok
                    msg=store.types[image](header,'jpeg',encoded.reshape(-1))
                writer.add_message(channels[name],ns+100_000_000,bytes(store.serialize_cdr(msg,typ)),ns)
        writer.finish()
    channels={
        '/right_joint_states':{'modality':'joint_space','fields':['position'],'source_kind':'feedback',
            'unit':'rad','side':'right','calibration_status':'confirmed',
            'representation':{'quantity':'joint_angle','semantic_status':'confirmed'}},
        '/right_end_pose':{'modality':'pose','fields':['position'],'source_kind':'feedback','unit':'m',
            'side':'right','calibration_status':'confirmed',
            'representation':{'quantity':'position','semantic_status':'confirmed'}},
        '/head_rgb':{'modality':'rgb','sensor_id':'head','representation':{'camera_role':'head'}}}
    for c in channels.values():c.update(time_source='acquisition',clock='synthetic_declared_clock')
    profile={'schema_version':1,'id':'migration_synthetic','version':'1','channels':channels,
        'camera_requirements':{'head':['rgb']},'calibration':{'Reset_Detection':{
            'status':'confirmed','reference':{'/right_joint_states':[0.,0.]},'unit':'rad','tolerance':.2}}}
    profile=confirm_numeric_profile(profile,'synthetic fixture author')
    (root/'robot.yaml').write_text(yaml.safe_dump(profile,allow_unicode=True))
    params={'Numeric_Check':{'channels':['/right_joint_states']},
        'Action_Smoothness':{'channels':['/right_end_pose']},
        'Video_Integrity':{'channels':['/head_rgb'],'input_mode':'frames','sample_step':1,'downscale':1},
        'Sync_Check':{'primary':'/head_rgb'},'Dedup_Check':{'channels':['/right_joint_states']}}
    (root/'params.json').write_text(json.dumps(params))


def worker(repository,root,label):
    sys.path.insert(0,str(repository))
    from framework.ir.dataset import open_dataset
    from framework.execution import run
    with open_dataset(root/'synthetic.mcap',profile=root/'robot.yaml') as ds:
        summary=run(ds,output=root/label,algorithm_ids=IDS,params=json.loads((root/'params.json').read_text()))
    print(json.dumps(summary))


def canonical(value,output):
    if isinstance(value,dict):return {k:canonical(v,output) for k,v in value.items() if k not in {'process_time_ms', 'adapter_elapsed_s'}}
    if isinstance(value,list):return [canonical(v,output) for v in value]
    if isinstance(value,str):return value.replace(str(output),'<OUTPUT>')
    return value


def compare(source,root):
    root.mkdir(parents=True,exist_ok=False)
    repository=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(repository));generate(root)
    for label,path in [('source',source),('destination',repository)]:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',str(path),str(root),label],
            cwd=path,text=True,capture_output=True)
        (root/(label+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+' worker failed: '+result.stderr[-2000:])
    rows=[]
    for ident in IDS:
        values=[]
        for label in ('source','destination'):
            path=next((root/label/'_runs').glob(f'*/{ident}/records.jsonl'))
            records=[json.loads(line) for line in path.read_text().splitlines()]
            values.append([canonical({k:r[k] for k in ('status','reason','result')},root/label) for r in records])
        rows.append({'algorithm':ident,'equal':values[0]==values[1],
                     'source_status':[r['status'] for r in values[0]],'destination_status':[r['status'] for r in values[1]]})
    result={'schema_version':'edc_cpu_comparison_v1','dataset':'synthetic ROS2 MCAP; same source and params',
        'normalization':['process_time_ms', 'adapter_elapsed_s', 'output artifact root'],'algorithms':rows,
        'passed':all(r['equal'] and r['destination_status']==['completed'] for r in rows)}
    (root/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])
    else:
        parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
        args=parser.parse_args();result=compare(args.source.resolve(),args.output.resolve())
        raise SystemExit(0 if result['passed'] else 1)
