from dataclasses import dataclass
from typing import Optional


@dataclass
class TaskRequest:
    source_path: str
    target_path: str
    start_time: str
    end_time: str


@dataclass
class TaskResult:
    success: bool
    code: str
    message: str
    details: Optional[str] = None


@dataclass
class GpsTelemetryWarning:
    message: str
    details: str
