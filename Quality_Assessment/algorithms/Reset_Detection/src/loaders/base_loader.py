from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.loader import Episode


class BaseLoader(ABC):
    @abstractmethod
    def load_episode(self, episode_path: Path) -> Episode:
        ...
