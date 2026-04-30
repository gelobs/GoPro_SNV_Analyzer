from typing import Callable, Optional

from backend.models import TaskRequest, TaskResult
from backend.ffmpeg_service import cut_video


class FfmpegRunner:
    def run(
        self,
        request: TaskRequest,
        log: Optional[Callable[[str], None]] = None,
    ) -> TaskResult:
        ok, details = cut_video(
            input_path=request.source_path,
            output_path=request.target_path,
            start_time=request.start_time,
            end_time=request.end_time,
            log=log,
        )

        if ok:
            return TaskResult(
                success=True,
                code="CUT_OK",
                message="Video cortado com sucesso.",
                details=details,
            )

        return TaskResult(
            success=False,
            code="CUT_ERROR",
            message="Falha ao cortar o video.",
            details=details,
        )
