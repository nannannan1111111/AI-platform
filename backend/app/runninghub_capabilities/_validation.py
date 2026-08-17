"""RunningHub 能力目录的内部校验。"""

import re

from app.runninghub_capabilities.models import (
    InvalidRunningHubCapability,
    RunningHubCapabilityInput,
    RunningHubInputCapability,
)

_INPUT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}").fullmatch


def required(value: str, field: str, *, maximum: int) -> str:
    """返回去除首尾空白后的必填短文本。"""
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise InvalidRunningHubCapability(f"{field}无效")
    return normalized


def input_capabilities(
    values: tuple[RunningHubInputCapability, ...],
) -> tuple[RunningHubInputCapability, ...]:
    """去重并按公开枚举顺序规范化粗粒度输入能力。"""
    if any(not isinstance(value, RunningHubInputCapability) for value in values):
        raise InvalidRunningHubCapability("输入能力无效")
    selected = set(values)
    return tuple(capability for capability in RunningHubInputCapability if capability in selected)


def availability(value: bool) -> bool:
    """只接受显式布尔可用状态。"""
    if not isinstance(value, bool):
        raise InvalidRunningHubCapability("可用状态无效")
    return value


def schema_inputs(values: tuple[RunningHubCapabilityInput, ...]) -> tuple[RunningHubCapabilityInput, ...]:
    """校验并规范化有序、能力内键唯一的用户公开输入。"""
    normalized: list[RunningHubCapabilityInput] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, RunningHubCapabilityInput):
            raise InvalidRunningHubCapability("输入 schema 无效")
        input_key = value.input_key.strip()
        if _INPUT_KEY(input_key) is None or input_key in seen:
            raise InvalidRunningHubCapability("输入键无效或重复")
        if not isinstance(value.kind, RunningHubInputCapability):
            raise InvalidRunningHubCapability("输入类型无效")
        if not isinstance(value.required, bool):
            raise InvalidRunningHubCapability("输入必填状态无效")
        normalized.append(
            RunningHubCapabilityInput(
                input_key=input_key,
                label=required(value.label, "输入名称", maximum=120),
                kind=value.kind,
                required=value.required,
            )
        )
        seen.add(input_key)
    return tuple(normalized)
