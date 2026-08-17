"""生成结果交付 Module 的公开模型。"""

from dataclasses import dataclass


class InvalidGenerationResult(ValueError):
    """已登记媒体不能作为当前图片任务的最终交付结果。"""


class InvalidGenerationOutputBatch(ValueError):
    """规范化生成输出批次不能安全登记到目标任务。"""


@dataclass(frozen=True, slots=True)
class GenerationOutput:
    """一项已经写入平台存储的规范化图片输出。"""

    result_reference: str
    object_key: str
    mime_type: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class GenerationImageContent:
    """一项等待平台存储和交付的规范化图片字节。"""

    result_reference: str
    mime_type: str
    content: bytes
