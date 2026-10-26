"""Optional task catalogue and explicit episode subtask annotations."""
import math


def validate_tasks(tasks):
    if not isinstance(tasks,list): raise ValueError('tasks must be a list')
    catalog={}
    for task in tasks:
        if isinstance(task,str): task={'id':task,'name':task} # Legacy task names remain supported.
        if not isinstance(task,dict) or not isinstance(task.get('id'),str) or not task['id']:
            raise ValueError('Task requires a nonempty string id')
        if task['id'] in catalog: raise ValueError('Duplicate task ID')
        for key in ('name','description'):
            if key in task and not isinstance(task[key],str): raise ValueError(f'Task {key} must be text')
        subtasks=task.get('subtasks',[])
        if not isinstance(subtasks,list): raise ValueError('subtasks must be a list')
        ids=set()
        for sub in subtasks:
            if not isinstance(sub,dict) or not isinstance(sub.get('id'),str) or not sub['id'] or sub['id'] in ids:
                raise ValueError('Subtasks require unique nonempty IDs within their task')
            ids.add(sub['id'])
            for key in ('name','description'):
                if key in sub and not isinstance(sub[key],str): raise ValueError(f'Subtask {key} must be text')
        catalog[task['id']]=task
    return catalog


def validate_episode_metadata(metadata,catalog):
    if not isinstance(metadata,dict): raise ValueError('Episode metadata must be a mapping')
    task_ids=metadata.get('task_ids',[])
    if not isinstance(task_ids,list) or any(not isinstance(t,str) or t not in catalog for t in task_ids) or len(set(task_ids))!=len(task_ids):
        raise ValueError('Episode task_ids must reference unique declared task IDs')
    segments=metadata.get('subtask_annotations',[])
    if not isinstance(segments,list): raise ValueError('subtask_annotations must be a list')
    for segment in segments:
        if not isinstance(segment,dict): raise ValueError('Subtask annotation must be a mapping')
        task=segment.get('task_id');sub=segment.get('subtask_id')
        if task not in task_ids or sub not in {s['id'] for s in catalog[task].get('subtasks',[])}:
            raise ValueError('Subtask annotation must reference an associated task and declared subtask')
        span=segment.get('span')
        if span is None: continue # Unlocalized annotations are valid.
        if not isinstance(span,dict) or span.get('unit') not in {'frame','s'}:
            raise ValueError('Subtask span requires unit frame or s')
        start,end=span.get('start'),span.get('end')
        if type(start) not in (float,int) or type(end) not in (float,int) or not math.isfinite(start) or not math.isfinite(end) or end<=start:
            raise ValueError('Subtask span requires finite start < end (half-open)')
        if span['unit']=='frame':
            if type(start) is not int or type(end) is not int or start<0 or not isinstance(span.get('channel'),str) or not span['channel']:
                raise ValueError('Frame spans require integer nonnegative bounds and an explicit channel')
        else:
            if not isinstance(span.get('clock'),str) or not span['clock'] or span.get('time_source') not in {'acquisition','container','playback','synthetic','unknown'}:
                raise ValueError('Second spans require explicit clock and time_source')
