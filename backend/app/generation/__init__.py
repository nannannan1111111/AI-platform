"""SaaS 生成任务 Module。"""

from app.generation._deadline_scheduler import GenerationDeadlineScheduler
from app.generation._memory import InMemoryGenerationTasks
from app.generation._sqlalchemy import SqlAlchemyGenerationTasks
from app.generation.deadlines import is_generation_timeout
from app.generation.interface import GenerationTasks
from app.generation.models import (
    GenerationActivitySummary,
    GenerationCancelled,
    GenerationConcurrencyLimit,
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationGlobalCapacityLimit,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTask,
    GenerationTaskAlreadyExists,
    GenerationTaskNotFound,
    GenerationTaskStatus,
    InvalidGenerationRequest,
)

__all__ = [
    "GenerationActivitySummary",
    "GenerationCancelled",
    "GenerationConcurrencyLimit",
    "GenerationDispatchStarted",
    "GenerationDeadlineScheduler",
    "GenerationFailed",
    "GenerationGlobalCapacityLimit",
    "GenerationParameters",
    "GenerationStarted",
    "GenerationSubmission",
    "GenerationSucceeded",
    "GenerationTask",
    "GenerationTaskAlreadyExists",
    "GenerationTaskNotFound",
    "GenerationTaskStatus",
    "GenerationTasks",
    "InMemoryGenerationTasks",
    "InvalidGenerationRequest",
    "is_generation_timeout",
    "SqlAlchemyGenerationTasks",
]
