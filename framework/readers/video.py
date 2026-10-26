"""CPU image decoding and video materialization with explicit frame provenance."""
from fractions import Fraction
from pathlib import Path
import hashlib
import io
import json
import tempfile
import numpy as np


def decode_image(payload):
    import av
    if isinstance(payload, np.ndarray) and payload.ndim >= 2:
        return payload
    with av.open(io.BytesIO(bytes(payload))) as container:
        return next(container.decode(video=0)).to_ndarray(format="rgb24")


def video_frames(path, start=None, end=None):
    import av
    with av.open(str(path)) as container:
        index = 0
        for frame in container.decode(video=0):
            timestamp = frame.time
            if timestamp is None:
                raise ValueError("Video frame has no presentation timestamp")
            if start is not None and timestamp < start - 1e-7:
                continue
            if end is not None and timestamp >= end - 1e-7:
                break
            yield {"index": index, "playback_time": timestamp - (start or 0),
                   "source_time": timestamp, "image": frame.to_ndarray(format="rgb24")}
            index += 1


def encode_frames(frames, output, *, fps=None, expected_count=None):
    import av
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # All writes are staged; incomplete videos cannot become evidence.
    temporary = output.with_name(output.stem + '.partial.mp4')
    iterator = iter(frames)
    buffered = []
    for _ in range(2):
        try:
            buffered.append(next(iterator))
        except StopIteration:
            break
    if not buffered:
        raise ValueError("No decodable frames")
    if fps is None:
        if len(buffered) < 2 or any(frame.get('playback_time') is None for frame in buffered):
            raise ValueError("Materialization requires explicit FPS or a source timeline")
        dt = buffered[1]['playback_time'] - buffered[0]['playback_time']
        if dt <= 0:
            raise ValueError("Non-increasing video timeline")
        fps = 1 / dt
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid FPS")
    import itertools
    mapping = []
    try:
        with av.open(str(temporary), 'w') as container:
            stream = container.add_stream('libx264', rate=Fraction(str(fps)).limit_denominator(100000))
            height, width = buffered[0]['image'].shape[:2]
            stream.width, stream.height = width + width % 2, height + height % 2
            stream.pix_fmt = 'yuv420p'
            for frame in itertools.chain(buffered, iterator):
                i = len(mapping)
                if frame['index'] != i:
                    raise ValueError("Non-contiguous source frame indices")
                pixels = np.asarray(frame['image'])
                fmt = 'gray' if pixels.ndim == 2 else 'rgb24'
                vf = av.VideoFrame.from_ndarray(pixels, format=fmt)
                vf = vf.reformat(width=stream.width, height=stream.height, format='yuv420p')
                for packet in stream.encode(vf):
                    container.mux(packet)
                mapping.append({'output_index': i, 'output_time': i / fps,
                                'source_time': frame.get('source_time'), 'source_index': frame.get('source_index', i)})
            for packet in stream.encode():
                container.mux(packet)
        if expected_count is not None and len(mapping) != expected_count:
            raise ValueError(f"Decoded frame count {len(mapping)} != expected {expected_count}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    from framework.results import write_json
    write_json(Path(str(output) + '.frames.json'), mapping)
    return str(output)


def cache_path(source, channel, selector=None):
    source = Path(source)
    stat = source.stat()
    token = json.dumps([str(source.resolve()), stat.st_size, stat.st_mtime_ns, channel, selector], sort_keys=True)
    directory = Path(tempfile.gettempdir()) / 'embodied_dqs_video'
    directory.mkdir(exist_ok=True)
    return directory / (hashlib.sha256(token.encode()).hexdigest() + '.mp4')
