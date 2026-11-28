"""A strict, read-only compatibility view for existing algorithm APIs."""
from edc.data.readers.base import DataReader


class EpisodeAccess(DataReader):
    def __init__(self, episode):
        self.episode = episode
        self.path = str(episode.path)

    def open(self, path=None):
        self.episode.open()
        return self.info()

    def info(self):
        return self.episode.describe_legacy()

    def close(self):
        self.episode.close()

    def read_field(self, info, name, field):
        return self.episode.read_signal(name, field)

    def channel_values(self, name, indices=None):
        return self.episode.read_values(name, indices)

    def iter_values(self, name):
        return iter(self.channel_values(name))

    def frames_at(self, name, indices):
        # 走公开接口（Episode.frames_at / CanonicalEpisode 白名单），不伸手拿包装层私有属性。
        try:
            return self.episode.frames_at(name, indices)
        except AttributeError:
            pass
        # 兜底：包装层未提供公开 frames_at 时，按公开 path 定位原始容器文件（.mcap）。
        from pathlib import Path
        src = Path(self.episode.path)
        cands = [src] if src.is_file() else sorted(src.rglob('*.mcap'))
        for c in cands:
            try:
                from edc.data.readers.mcap import McapReader
                r = McapReader()
                r.open(str(c))
                return r.frames_at(name, indices)
            except Exception:
                continue
        raise ValueError(f'{name}: image decoding unavailable')

    def channel_timestamps(self, name):
        return self.episode.channels[name].timestamps

    def materialize_image_channel(self, info, name, output_path=None):
        if output_path:
            return self.episode.materialize_video(name, output_path)
        return self.episode.video_source(name)

    def image_bytes(self, name):
        return ((f['index'], f['playback_time'], f['image']) for f in self.episode.iter_frames(name))
