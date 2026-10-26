"""Legacy single-source API backed by the canonical episode readers."""
from pathlib import Path
from .base import DataReader


class SingleEpisodeReader(DataReader):
    format = "episode"

    def __init__(self, hints=None):
        self.hints = hints or {}
        self.reader = None

    def open(self, path):
        from edc.data.ir.dataset import Episode
        from .structured import discover, open_structured
        source = Path(path).resolve()
        if self.format == "episode":
            episode = Episode(source.name, source, self.format, {})
        else:
            episodes = discover(source, {})
            if len(episodes) != 1:
                raise ValueError("Multiple episodes: use open_dataset().iter_episodes() or an explicit selector")
            episode = episodes[0]
            if "discovery_error" in episode.selector:
                raise ValueError(episode.selector["discovery_error"])
        self.reader, self._info = open_structured(episode)
        return self._info

    def close(self):
        if self.reader:
            self.reader.close()

    def channel_values(self, name, indices=None):
        return self.reader.channel_values(name, indices)

    def image_storage(self, name):
        return self.reader.image_storage(name)

    def iter_frames(self, name):
        return self.reader.iter_frames(name)

    def materialize_image_channel(self, info, name, output_path=None):
        return self.reader.materialize_image_channel(info, name, output_path)
