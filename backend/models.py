from dataclasses import dataclass
from typing import List, Optional


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
    failure_times: Optional[List[str]] = None


@dataclass
class AccelerometerTelemetryWarning:
    message: str
    details: str
    failure_times: Optional[List[str]] = None
