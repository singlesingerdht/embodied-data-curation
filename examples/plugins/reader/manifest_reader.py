"""Reader template: explicit custom manifest reusing existing episode readers."""
from dataclasses import dataclass
import json
from edc.data.ir.dataset import Dataset, Episode


@dataclass(frozen=True)
class ReaderSpec:
    id: str = "team_example__manifest"
    version: str = "0.1.0"
    interface_version: int = 1


class ManifestReader:
    spec = ReaderSpec()

    def can_open(self, path):
        return path.is_dir() and (path / "custom_manifest.json").is_file()

    def open_dataset(self, path, profile):
        manifest = json.loads((path / "custom_manifest.json").read_text())
        episodes = []
        for row in manifest["episodes"]:
            source = (path / row["path"]).resolve()
            if not source.is_relative_to(path):
                raise ValueError("Episode escapes dataset root")
            episodes.append(Episode(row["id"], source, row["format"], profile, row.get("selector", {}), metadata=row.get("metadata", {})))
        if not episodes or len({e.id for e in episodes}) != len(episodes):
            raise ValueError("Require unique episode IDs")
        return Dataset(path, profile, manifest, episodes)


plugin = ManifestReader()
