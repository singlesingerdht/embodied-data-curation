"""Explicit registered depth only; no guessed units, resized maps or guessed intrinsics."""
from pathlib import Path
import numpy as np
from .spatial_geometry import fit_support_plane, object_geometry


def validate_inputs(dataset, params):
    cfg=params['spatial']
    if cfg.get('source') not in {'sensor_depth','monocular_estimate'}:
        raise ValueError('Declare sensor_depth or monocular_estimate')
    if cfg.get('unit') not in {'m','mm'} or cfg.get('registered_to_rgb') is not True:
        raise ValueError('Explicit depth units and verified RGB registration required')
    if not cfg.get('registration_id'):
        raise ValueError('A registration/calibration reference is required')
    k=cfg['intrinsics']
    if not all(np.isfinite(k[x]) for x in ('fx','fy','cx','cy')) or min(k['fx'],k['fy'])<=0:
        raise ValueError('Invalid camera intrinsics')
    if cfg['source']=='monocular_estimate' and not Path(cfg.get('model','')).is_dir():
        raise ValueError('Monocular estimate requires the originating local model directory')
    ids={e.id for e in dataset.iter_episodes()}
    if set(cfg['episodes']) != ids: raise ValueError('Depth mapping must match selected stable episode IDs exactly')
    for item in cfg['episodes'].values():
        if not Path(item['path']).is_file() or item.get('rgb_frame_index') != params['frame_index']:
            raise ValueError('Registered depth file/frame mapping missing')
        if 'depth_source_index' not in item or not item.get('correspondence_evidence'):
            raise ValueError('Provide depth source index and RGB-depth correspondence evidence')


def describe_scene(row, cfg, episode_id, workdir, key):
    item=cfg['episodes'][episode_id]
    depth=np.load(item['path'],allow_pickle=False)
    if depth.ndim!=2 or depth.shape!=(row['height'],row['width']):
        raise ValueError('Registered depth must match RGB dimensions; automatic resizing is unsupported')
    depth=depth.astype(float)*(0.001 if cfg['unit']=='mm' else 1.0)
    instances=[(name,i,x) for name,c in row['categories'].items() for i,x in enumerate(c['instances'])]
    masks=[x['_mask'] for _,_,x in instances]
    np.savez_compressed(workdir/f'{key}-masks.npz',**{f'mask_{i}':m for i,m in enumerate(masks)})
    plane=fit_support_plane(depth,masks,cfg['intrinsics'],cfg.get('plane',{}))
    objects=[{'category':name,'instance':i,**object_geometry(x['_mask'],depth,plane,cfg['intrinsics'])}
             for name,i,x in instances]
    return {'coordinate_frame':'camera','depth_source':cfg['source'],'depth_unit':'m',
            'registration_id':cfg['registration_id'],'correspondence':item,
            'intrinsics':cfg['intrinsics'],'support_plane':plane,'objects':objects,
            'contributes_to_2d_score':False,'masks':f'{key}-masks.npz'}
