"""生成尝试共享不变量。"""

from hashlib import sha256

from app.generation import GenerationTask
from app.generation_attempts.models import GenerationAttemptConflict, GenerationAttemptPreparation


def selected_route(task: GenerationTask, preparation: GenerationAttemptPreparation) -> str:
    """要求每次生成尝试使用任务提交时固化的模型路由。"""
    route_id = preparation.route_id.strip()
    if not route_id or route_id != task.selected_route_id:
        raise GenerationAttemptConflict("生成尝试必须使用任务已固化的模型路由")
    return route_id


def provider_idempotency_key(task_id: str, attempt_no: int, route_id: str) -> str:
    """派生不包含凭据且长度稳定的 Provider 幂等键。"""
    payload = f"{task_id}\0{attempt_no}\0{route_id}".encode()
    return f"gen_{sha256(payload).hexdigest()}"
