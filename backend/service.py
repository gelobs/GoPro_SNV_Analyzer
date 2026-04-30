from typing import Callable, Optional

from backend.models import TaskRequest, TaskResult
from backend.runner import FfmpegRunner
from backend.validator import validate_request


class TaskService:
    def __init__(self) -> None:
        self.runner = FfmpegRunner()

    def execute(
        self,
        request: TaskRequest,
        log: Optional[Callable[[str], None]] = None,
    ) -> TaskResult:
        error = validate_request(request)
        if error:
            return TaskResult(
                success=False,
                code="VALIDATION_ERROR",
                message=error,
            )

        return self.runner.run(request, log=log)
