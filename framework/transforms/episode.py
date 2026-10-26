"""Bind explicit transformations to Episode payloads without modifying source IR."""
import numpy as np
from .core import RobotTransforms, convert_units


def provenance(episode, channel=None, field=None):
    return {'episode_id':episode.id,'source':str(episode.path),'selector':episode.selector,
            'channel':channel,'field':field,'rows':'identity',
            'selection':episode.channels[channel].selection if channel is not None and hasattr(episode.channels[channel], 'selection') else {}}


def signal(episode, channel, field, *, target_unit=None, target_frame=None, kind=None):
    episode.open()
    c=episode.channels[channel];spec=c.field_spec(field)
    values=episode.read_signal(channel,field)
    steps=[]
    if target_unit is not None:
        result=convert_units(values,spec['unit'],target_unit)
        values=result.output;steps.append(result.params)
    if target_frame is not None:
        if kind is None: raise ValueError('Coordinate transform requires explicit kind (points/vectors/poses)')
        result=RobotTransforms(episode.profile).coordinates(values,spec['coordinate_frame'],target_frame,
            kind=kind,unit=target_unit or spec['unit'])
        values=result.output;steps.append(result.params)
    if not steps: raise ValueError('Specify target_unit or target_frame')
    from .core import TransformResult
    return TransformResult(values,{'operation':'signal','steps':steps,'input_semantics':spec},provenance(episode,channel,field))


def timestamps(episode, channel, target_clock):
    episode.open();c=episode.channels[channel]
    if c.timestamps is None: raise ValueError('Channel has no timeline')
    result=RobotTransforms(episode.profile).clock(c.timestamps,c.clock,target_clock,time_source=c.time_source)
    result.source_mapping=provenance(episode,channel)
    return result


def fk(episode, chain_name):
    episode.open();transforms=RobotTransforms(episode.profile)
    chain=transforms.chains[chain_name]
    values={};mappings={};timeline=None;clock=None;time_source=None;source_kind=None
    for joint in chain['joints']:
        if joint['type']=='fixed': continue
        binding=joint.get('input')
        if not isinstance(binding,dict) or set(binding)!={'channel','field','index'}:
            raise ValueError('Episode FK requires input: {channel, field, index} for each moving joint')
        c=episode.channels[binding['channel']];spec=c.field_spec(binding['field'])
        data=episode.read_signal(c.name,binding['field']);index=binding['index']
        if type(index) is not int or data.ndim!=2 or not 0<=index<data.shape[1]: raise ValueError('Invalid FK input index/shape')
        if spec['joint_names'] and (index >= len(spec['joint_names']) or spec['joint_names'][index] != joint['name']):
            raise ValueError('FK input index contradicts declared joint_names')
        if c.timestamps is None or c.clock is None or len(c.timestamps)!=len(data): raise ValueError('FK requires explicit input timelines and clocks')
        ts=np.asarray(c.timestamps,dtype=float)
        if not np.isfinite(ts).all() or np.any(np.diff(ts)<=0): raise ValueError('FK requires finite strictly increasing timelines')
        if timeline is None:
            timeline=ts;clock=c.clock;time_source=c.time_source;source_kind=spec['source_kind']
        elif clock!=c.clock or time_source!=c.time_source or source_kind!=spec['source_kind'] or not np.array_equal(timeline,ts):
            raise ValueError('FK inputs must have identical timelines, clocks and command/feedback semantics; align explicitly first')
        values[joint['name']]=convert_units(data[:,index],spec['unit'],joint['unit']).output
        mappings[joint['name']]={**provenance(episode,c.name,binding['field']),'index':index,'input_unit':spec['unit']}
    result=transforms.fk(chain_name,values)
    result.source_mapping={**provenance(episode),'joint_inputs':mappings,
        'timestamps':timeline.tolist() if timeline is not None else None,'clock':clock,'time_source':time_source,'source_kind':source_kind}
    return result
