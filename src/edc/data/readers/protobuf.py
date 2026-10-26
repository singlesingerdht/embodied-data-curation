"""Descriptor-driven MCAP decoding; no generated vendor modules or physical guesses."""
import numpy as np


def message_class(schema):
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    if schema is None or schema.encoding != 'protobuf' or not schema.data:
        raise ValueError('Protobuf requires an embedded FileDescriptorSet')
    try:
        files = descriptor_pb2.FileDescriptorSet.FromString(schema.data).file
    except Exception as exc:
        raise ValueError('Invalid Protobuf FileDescriptorSet') from exc
    pool = descriptor_pool.DescriptorPool()
    pending = {f.name: f for f in files}
    while pending:
        ready = [f for f in pending.values() if all(d not in pending for d in f.dependency)]
        if not ready:
            raise ValueError('Cyclic Protobuf descriptor dependencies')
        for file in ready:
            try:
                pool.Add(file)
            except Exception as exc:
                raise ValueError(f'Invalid/missing Protobuf descriptor dependency: {file.name}') from exc
            del pending[file.name]
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(schema.name))


def numeric_values(message, prefix='', depth=0):
    """Flatten numeric fields to literal dotted keys; preserve original field names."""
    if depth > 32:
        raise ValueError('Protobuf nesting exceeds 32 levels')
    result = {}
    for field in message.DESCRIPTOR.fields:
        # Header/time values are metadata, not robot numeric signals.
        if not prefix and field.name in {'header', 'timestamp'}:
            continue
        name = prefix + field.name
        value = getattr(message, field.name)
        repeated = field.is_repeated if hasattr(field, 'is_repeated') else field.label == field.LABEL_REPEATED
        if field.type == field.TYPE_MESSAGE:
            if repeated:  # Ragged repeated messages/maps need explicit schema-specific support.
                continue
            if message.HasField(field.name):
                result.update(numeric_values(value, name + '.', depth + 1))
        elif field.type not in {field.TYPE_STRING, field.TYPE_BYTES}:
            if repeated:
                if len(value):
                    result[name] = list(value)
            elif not field.has_presence or message.HasField(field.name):
                result[name] = value
    return result


def image(message):
    """foxglove RGB/gray images; honor stride, reject unsupported pixel layouts."""
    from .video import decode_image
    if message.DESCRIPTOR.full_name == 'foxglove.CompressedImage':
        return decode_image(message.data)
    if message.DESCRIPTOR.full_name != 'foxglove.RawImage':
        raise ValueError('Unsupported Protobuf image schema')
    layouts = {'rgb8': 3, 'bgr8': 3, 'rgba8': 4, 'bgra8': 4, 'mono8': 1, '8UC1': 1}
    encoding = message.encoding
    if encoding not in layouts:
        raise ValueError(f'Unsupported Protobuf image encoding: {encoding}')
    width, height, step = message.width, message.height, message.step
    channels = layouts[encoding]
    if width <= 0 or height <= 0 or step < width * channels or len(message.data) != height * step:
        raise ValueError('Invalid Protobuf image dimensions/stride/payload length')
    pixels = np.frombuffer(message.data, dtype=np.uint8).reshape(height, step)[:, :width * channels]
    pixels = pixels.reshape(height, width, channels)
    if encoding in {'bgr8', 'bgra8'}:
        pixels = pixels[:, :, [2, 1, 0]]
    elif channels == 4:
        pixels = pixels[:, :, :3]
    elif channels == 1:
        pixels = np.repeat(pixels, 3, axis=2)
    return np.ascontiguousarray(pixels)
