from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicRecord:
    name: str
    type: str | None = None
    serialization_format: str | None = None
    message_count: int | None = None


@dataclass(frozen=True)
class BagRecord:
    path: str
    name: str
    root_relative_path: str | None = None
    storage_identifier: str | None = None
    starting_time: str | None = None
    duration_ns: int | None = None
    message_count: int | None = None
    size_bytes: int = 0
    status: str = "broken"
    error_message: str | None = None
    index_signature: str = ""
    topics: list[TopicRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ScanResult:
    scanned: int = 0
    valid: int = 0
    broken: int = 0
    duration_seconds: float = 0.0

    def increment(self, status: str) -> "ScanResult":
        counts = {
            "scanned": self.scanned + 1,
            "valid": self.valid,
            "broken": self.broken,
            "duration_seconds": self.duration_seconds,
        }
        if status in counts:
            counts[status] += 1
        else:
            counts["broken"] += 1
        return ScanResult(**counts)
