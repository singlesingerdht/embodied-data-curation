"""NumPy-only units, affine clocks, rigid frames, serial FK and camera projection.

Rigid matrices map source coordinates into target coordinates, using column
vectors. FK joint origins map joint coordinates to the preceding link.
"""
from dataclasses import dataclass
import copy
import numpy as np

# (physical dimension, multiplier to the SI base unit). No current/torque alias.
UNITS = {
    'm': ('length', 1.), 'cm': ('length', .01), 'mm': ('length', .001),
    'rad': ('angle', 1.), 'deg': ('angle', np.pi/180),
    's': ('time', 1.), 'ms': ('time', .001), 'us': ('time', 1e-6), 'ns': ('time', 1e-9),
    'm/s': ('velocity', 1.), 'mm/s': ('velocity', .001),
    'm/s^2': ('acceleration', 1.), 'mm/s^2': ('acceleration', .001),
    'rad/s': ('angular_velocity', 1.), 'deg/s': ('angular_velocity', np.pi/180),
    'rad/s^2': ('angular_acceleration', 1.), 'deg/s^2': ('angular_acceleration', np.pi/180),
    'A': ('current', 1.), 'mA': ('current', .001),
    'N': ('force', 1.), 'N*m': ('torque', 1.), 'N*mm': ('torque', .001),
    '1': ('dimensionless', 1.),
}


@dataclass
class TransformResult:
    output: np.ndarray
    params: dict
    source_mapping: dict

    def describe(self):
        return {'output': self.output.tolist(), 'params': self.params,
                'source_mapping': self.source_mapping}


def convert_units(values, source_unit, target_unit):
    if source_unit not in UNITS or target_unit not in UNITS:
        raise ValueError('Unknown/unsupported unit; declare a supported physical unit')
    source, target = UNITS[source_unit], UNITS[target_unit]
    if source[0] != target[0]:
        raise ValueError(f'Incompatible units: {source_unit} -> {target_unit}')
    factor = source[1]/target[1]
    return TransformResult(np.asarray(values, dtype=float)*factor,
        {'operation':'units', 'source_unit':source_unit, 'target_unit':target_unit, 'factor':factor},
        {'rows':'identity', 'elements':'identity'})


def finite(value, shape=None):
    array = np.asarray(value, dtype=float)
    if (shape is not None and array.shape != shape) or not np.isfinite(array).all():
        raise ValueError(f'Expected finite array of shape {shape}')
    return array


def rigid(value):
    matrix = finite(value, (4,4))
    r = matrix[:3,:3]
    if not np.allclose(matrix[3], [0,0,0,1], atol=1e-8, rtol=0) or not np.allclose(r.T@r,np.eye(3),atol=1e-8,rtol=0) or not np.isclose(np.linalg.det(r),1,atol=1e-8,rtol=0):
        raise ValueError('Expected a proper rigid transform (orthonormal rotation, determinant +1)')
    return matrix


def rotation(axis, angle):
    x,y,z = axis
    skew=np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
    return np.eye(3)+np.sin(angle)*skew+(1-np.cos(angle))*(skew@skew)


def named_mapping(value, label):
    if not isinstance(value,dict) or any(not isinstance(k,str) or not k or not isinstance(v,dict) for k,v in value.items()):
        raise ValueError(f'{label} must be a mapping of names to objects')
    return value


class RobotTransforms:
    def __init__(self, profile):
        self.config = copy.deepcopy(profile.get('transforms', {}))
        if not isinstance(self.config,dict) or set(self.config)-{'frames','clocks','chains','cameras'}:
            raise ValueError('Unknown transforms sections')
        self.frames=named_mapping(self.config.get('frames',{}),'frames')
        self.clocks=named_mapping(self.config.get('clocks',{}),'clocks')
        self.chains=named_mapping(self.config.get('chains',{}),'chains')
        self.cameras=named_mapping(self.config.get('cameras',{}),'cameras')
        for name, chain in list(self.chains.items()):
            if 'urdf' in chain:
                if set(chain) - {'urdf', 'base_frame', 'tip_frame', 'inputs'}:
                    raise ValueError('URDF chains accept urdf/base_frame/tip_frame/inputs only')
                from .urdf import import_urdf_chain
                self.chains[name] = import_urdf_chain(chain['urdf'], chain['base_frame'], chain['tip_frame'],
                                                     inputs=chain.get('inputs'))
        self._validate()

    def _validate(self):
        # Static frame graph is a forest: unique paths, no competing calibrations.
        parents={}
        for name,item in self.frames.items():
            a,b=item.get('source'),item.get('target')
            if not isinstance(a,str) or not isinstance(b,str) or not a or not b or a==b:
                raise ValueError(f'{name}: distinct source/target frame names required')
            rigid(item.get('matrix'))
            if item.get('translation_unit') not in {'m','cm','mm'}:
                raise ValueError(f'{name}: explicit translation_unit required')
            def root(x):
                while x in parents: x=parents[x]
                return x
            ra,rb=root(a),root(b)
            if ra==rb: raise ValueError('Frame graph must have a unique path (no cycles/duplicate edges)')
            parents[ra]=rb
        clock_pairs=set()
        for name,item in self.clocks.items():
            a,b=item.get('source'),item.get('target')
            if not isinstance(a,str) or not isinstance(b,str) or not a or not b or a==b:
                raise ValueError(f'{name}: distinct source/target clocks required')
            pair=frozenset((a,b))
            if pair in clock_pairs: raise ValueError('Duplicate clock mapping')
            clock_pairs.add(pair)
            scale=float(item.get('scale',1));offset=float(item.get('offset_seconds',0))
            if not np.isfinite([scale,offset]).all() or scale<=0: raise ValueError('Clock scale must be positive and finite')
            if 'valid_range' in item:
                bounds=finite(item['valid_range'],(2,))
                if bounds[1]<bounds[0]: raise ValueError('Invalid clock valid_range')
        for name,chain in self.chains.items():
            if not chain.get('base_frame') or not chain.get('tip_frame') or chain['base_frame']==chain['tip_frame']:
                raise ValueError(f'{name}: distinct base_frame/tip_frame required')
            joints=chain.get('joints')
            if not isinstance(joints,list) or not joints: raise ValueError('Chain requires ordered joints')
            names=set()
            for joint in joints:
                if not isinstance(joint,dict) or not isinstance(joint.get('name'),str) or not joint['name'] or joint['name'] in names:
                    raise ValueError('Joint names must be unique')
                names.add(joint['name'])
                kind=joint.get('type')
                if kind not in {'fixed','revolute','prismatic'}: raise ValueError('Unsupported joint type')
                rigid(joint.get('origin'))
                if joint.get('translation_unit') not in {'m','cm','mm'}: raise ValueError('Joint origin translation_unit required')
                if kind!='fixed':
                    axis=finite(joint.get('axis'),(3,))
                    if not np.isclose(np.linalg.norm(axis),1,atol=1e-8,rtol=0): raise ValueError('Joint axis must be a unit vector')
                    unit=joint.get('unit')
                    convert_units([0],unit,'rad' if kind=='revolute' else 'm')
                    if 'limits' in joint:
                        bounds=finite(joint['limits'],(2,))
                        if bounds[1]<bounds[0]: raise ValueError('Invalid joint limits')
        for name,camera in self.cameras.items():
            if not camera.get('frame') or not camera.get('channel'): raise ValueError('Camera frame/channel required')
            size=camera.get('image_size')
            if not isinstance(size,list) or len(size)!=2 or any(type(v) is not int or v<=0 for v in size): raise ValueError('image_size must be [width,height]')
            k=finite(camera.get('K'),(3,3))
            if k[0,0]<=0 or k[1,1]<=0 or not np.allclose(k[2],[0,0,1]) or k[1,0]!=0 or k[0,1]!=0:
                raise ValueError('K must be a positive-focal, zero-skew pinhole intrinsic matrix')
            model=camera.get('distortion_model')
            if model not in {'none','plumb_bob'}: raise ValueError('Supported camera models: none/plumb_bob')
            finite(camera.get('distortion',[]),(0 if model=='none' else 5,))

    def clock(self, values, source, target, *, time_source='unknown'):
        if time_source not in {'acquisition','container','playback','synthetic','unknown'}:
            raise ValueError('Invalid time source')
        times=finite(values)
        if times.ndim!=1: raise ValueError('Clock input must be a one-dimensional timeline')
        if not source or not target: raise ValueError('Explicit clocks required')
        if source==target:
            return TransformResult(times.copy(),{'operation':'clock','source':source,'target':target,'time_source':time_source},{'rows':'identity'})
        candidates=[(n,c) for n,c in self.clocks.items() if {c['source'],c['target']}=={source,target}]
        if len(candidates)!=1: raise ValueError('Exactly one explicit direct clock mapping required')
        name,c=candidates[0];scale=float(c.get('scale',1));offset=float(c.get('offset_seconds',0))
        forward=source==c['source']
        source_times=times if forward else (times-offset)/scale
        if 'valid_range' in c and np.any((source_times<c['valid_range'][0])|(source_times>c['valid_range'][1])):
            raise ValueError('Clock mapping outside valid_range; extrapolation forbidden')
        output=times*scale+offset if forward else source_times
        if not np.isfinite(output).all(): raise ValueError('Clock transform overflow')
        return TransformResult(output,{'operation':'clock','mapping':name,'definition':c,'inverse':not forward,
            'source':source,'target':target,'time_source':time_source}, {'rows':'identity'})

    def frame_matrix(self, source, target):
        if not source or not target: raise ValueError('Explicit coordinate frames required')
        if source==target: return np.eye(4),[]
        graph={}
        for name,item in self.frames.items():
            matrix=rigid(item['matrix']).copy()
            matrix[:3,3]=convert_units(matrix[:3,3],item['translation_unit'],'m').output
            graph.setdefault(item['source'],[]).append((item['target'],matrix,name,False))
            graph.setdefault(item['target'],[]).append((item['source'],np.linalg.inv(matrix),name,True))
        queue=[(source,np.eye(4),[])];seen={source}
        for node,matrix,path in queue:
            for nxt,edge,name,inverse in graph.get(node,[]):
                if nxt in seen: continue
                composed=edge@matrix;trace=path+[{'name':name,'inverse':inverse,'definition':self.frames[name]}]
                if nxt==target: return composed,trace
                seen.add(nxt);queue.append((nxt,composed,trace))
        raise ValueError(f'No frame path: {source} -> {target}')

    def coordinates(self, values, source, target, *, kind='points', unit='m'):
        matrix,path=self.frame_matrix(source,target)
        data=finite(values)
        if kind=='poses':
            if data.shape[-2:]!=(4,4): raise ValueError('poses require (...,4,4) rigid matrices')
            data=data.copy()
            for pose in data.reshape(-1,4,4): rigid(pose)
            data[...,:3,3]=convert_units(data[...,:3,3],unit,'m').output
            output=matrix@data
            output_unit='m'
        elif kind in {'points','vectors'}:
            if not data.ndim or data.shape[-1]!=3: raise ValueError('points/vectors require (...,3)')
            if kind=='points':
                data=convert_units(data,unit,'m').output;output_unit='m'
            else:
                if unit not in UNITS: raise ValueError('Explicit supported vector unit required')
                output_unit=unit
            output=data@matrix[:3,:3].T
            if kind=='points': output=output+matrix[:3,3]
        else: raise ValueError('kind must be points, vectors or poses')
        return TransformResult(output,{'operation':'coordinates','kind':kind,'source':source,'target':target,
            'input_unit':unit,'output_unit':output_unit,'path':path}, {'rows':'identity'})

    def fk(self, chain_name, positions):
        chain=self.chains[chain_name]
        moving=[j for j in chain['joints'] if j['type']!='fixed']
        if set(positions)!={j['name'] for j in moving}: raise ValueError('FK requires exactly the named moving joints')
        arrays={k:finite(v) for k,v in positions.items()}
        shapes={v.shape for v in arrays.values()}
        if len(shapes)>1: raise ValueError('FK joint sample shapes differ; no broadcasting or implicit alignment')
        shape=next(iter(shapes),())
        output=np.broadcast_to(np.eye(4),shape+(4,4)).copy()
        for joint in chain['joints']:
            origin=rigid(joint['origin']).copy()
            origin[:3,3]=convert_units(origin[:3,3],joint['translation_unit'],'m').output
            output=output@origin
            if joint['type']=='fixed': continue
            values=arrays[joint['name']]
            if 'limits' in joint and np.any((values<joint['limits'][0])|(values>joint['limits'][1])):
                raise ValueError(f"Joint limits exceeded: {joint['name']}")
            values=convert_units(values,joint['unit'],'rad' if joint['type']=='revolute' else 'm').output
            motion=np.broadcast_to(np.eye(4),shape+(4,4)).copy()
            if joint['type']=='revolute':
                flat=motion.reshape(-1,4,4)
                for i,angle in enumerate(values.reshape(-1)): flat[i,:3,:3]=rotation(joint['axis'],angle)
            else: motion[...,:3,3]=values[...,None]*np.asarray(joint['axis'])
            output=output@motion
        return TransformResult(output,{'operation':'fk','chain':chain_name,'definition':chain,
            'source':chain['tip_frame'],'target':chain['base_frame'],'translation_unit':'m'},
            {'rows':'identity','joints':list(positions)})

    def project(self, camera_name, points, *, source_frame, unit='m'):
        camera=self.cameras[camera_name]
        result=self.coordinates(points,source_frame,camera['frame'],unit=unit)
        xyz=result.output
        if np.any(xyz[...,2]<=0): raise ValueError('Camera projection requires positive depth for every point')
        x,y=xyz[...,0]/xyz[...,2],xyz[...,1]/xyz[...,2]
        if camera['distortion_model']=='plumb_bob':
            k1,k2,p1,p2,k3=camera['distortion'];r2=x*x+y*y
            radial=1+k1*r2+k2*r2*r2+k3*r2*r2*r2
            x,y=x*radial+2*p1*x*y+p2*(r2+2*x*x), y*radial+p1*(r2+2*y*y)+2*p2*x*y
        k=np.asarray(camera['K']);uv=np.stack([k[0,0]*x+k[0,2],k[1,1]*y+k[1,2]],axis=-1)
        if not np.isfinite(uv).all(): raise ValueError('Projection overflow')
        return TransformResult(uv,{'operation':'project','camera':camera_name,'definition':camera,
            'coordinate_transform':result.params,'output_unit':'pixel','clips_to_image':False},result.source_mapping)
