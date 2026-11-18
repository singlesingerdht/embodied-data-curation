"""Prepare explicitly labelled DA3 estimates from EDC RGB frames, outside raw data."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from edc.data.ir.canonical import open_dataset
from edc.algorithms.value.runtime import fingerprint
from edc.algorithms.channel_names import resolve_channel
from edc.reporting.results import write_json


def prepare(dataset, robot, model_path, output, *, channel='faceImg', episode_index=0,
            frame_range=None, device='cuda:0', depth_scale=1.0):
    output=Path(output).resolve()
    source=Path(dataset).resolve()
    if output==source or output.is_relative_to(source):
        raise ValueError('Depth estimates must be outside raw data')
    if not np.isfinite(depth_scale) or depth_scale<=0:
        raise ValueError('depth_scale must be finite and positive')
    with open_dataset(dataset,profile=robot) as ds:
        episodes=list(ds.iter_episodes())
        if not 0 <= episode_index < len(episodes):
            raise ValueError('Episode index is outside the dataset')
        ep=episodes[episode_index]
        with ep.opened():
            channel=resolve_channel(ep,channel)
            if output==Path(ep.path).resolve() or output.is_relative_to(Path(ep.path).resolve()):
                raise ValueError('Output is inside the selected raw episode')
            start,end=frame_range or [0,ep.channels[channel].count]
            if not 0<=start<end<=ep.channels[channel].count:raise ValueError('Invalid frame range')
            output.mkdir(parents=True,exist_ok=False)
            record={'source':'monocular_estimate','method':'DA3NESTED-GIANT-LARGE-1.1',
                    'model':fingerprint(model_path),'depth_scale':depth_scale,
                    'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'status':'preparing','unit':'m','frames':[],
                    'episode_id':ep.id,'source_episode':str(ep.path),'selector':ep.selector,
                    'channel':channel,'frame_range':[start,end]}
            write_json(output/'provenance.json',record)
            # Validate output boundaries before writes; save provenance before model init.
            from depth_anything_3.api import DepthAnything3
            import cv2
            model=DepthAnything3.from_pretrained(str(model_path)).to(device).eval()
            array=None
            for row in ep.iter_frames(channel):
                i=row['index']
                if i<start:continue
                if i>=end:break
                rgb=np.asarray(row['image']);h,w=rgb.shape[:2]
                if array is None:
                    array=np.lib.format.open_memmap(output/'depth.npy',mode='w+',dtype=np.float32,shape=(end-start,h,w))
                elif array.shape[1:] != (h,w):
                    raise ValueError('RGB dimensions change within the selected range')
                pred=model.inference(image=[rgb],export_format='mini_npz')
                depth=cv2.resize(pred.depth[0],(w,h),interpolation=cv2.INTER_LINEAR)*depth_scale
                if not np.isfinite(depth).all() or np.any(depth<0) or not np.any(depth>0):
                    raise ValueError(f'Invalid estimated depth at source frame {i}')
                array[i-start]=depth
                record['frames'].append({'reference_index':i,'source_index':int(row.get('source_index',i)),
                    'rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest(),
                    'estimated_intrinsics':pred.intrinsics[0].tolist(),
                    'model_shape':list(pred.depth[0].shape),'rgb_shape':[h,w]})
                if (i-start)%30==0:
                    array.flush();write_json(output/'provenance.json',record)
                    print(f'Depth {i-start+1}/{end-start}',flush=True)
            if array is None or len(record['frames'])!=end-start:raise ValueError('RGB coverage incomplete')
            array.flush()
            record.update(status='completed',registered_to_rgb=True,
                registration_id='same-original-rgb-frame-resized-prediction',
                registration_method='Bilinear resize of each model prediction to its own input RGB; not sensor calibration',
                depth=fingerprint(output/'depth.npy'))
            write_json(output/'provenance.json',record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('dataset');p.add_argument('--robot',required=True)
    p.add_argument('--model',required=True);p.add_argument('--output',required=True)
    p.add_argument('--channel',default='faceImg');p.add_argument('--episode',type=int,default=0)
    p.add_argument('--frame-range',type=int,nargs=2);p.add_argument('--device',default='cuda:0')
    p.add_argument('--depth-scale',type=float,default=1.0,
                   help='Explicit legacy scale for reproduction; never a calibration claim')
    a=p.parse_args();prepare(a.dataset,a.robot,a.model,a.output,channel=a.channel,
        episode_index=a.episode,frame_range=a.frame_range,device=a.device,depth_scale=a.depth_scale)


if __name__=='__main__':main()
