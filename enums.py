from enum import StrEnum, auto


class JobStatus(StrEnum):
    QUEUED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    TIMEOUT = auto()
