from datetime import UTC, datetime

import pytest

from app.generation._validation import validated_submission
from app.generation.models import GenerationParameters, GenerationSubmission, InvalidGenerationRequest


def _submission(model: str) -> GenerationSubmission:
    return GenerationSubmission(
        user_id="user-1",
        account_space_id="space-1",
        canvas_id=None,
        task_id="task-1",
        logical_model=model,
        output_spec="1k",
        quantity=1,
        prompt="replace the marked object",
        params=GenerationParameters(aspect_ratio="1:1", operation="inpaint", input_fidelity="high"),
        submitted_at=datetime.now(UTC),
        reference_media_ids=("source-1",),
        mask_media_id="mask-1",
    )


@pytest.mark.parametrize("model", ["image2", "image-2", "gptimage2", "foo-image2-pro", "foo-gpt-image-2-bar"])
def test_inpaint_accepts_image2_aliases(model: str) -> None:
    validated_submission(_submission(model))


def test_inpaint_does_not_accept_image21_as_image2() -> None:
    with pytest.raises(InvalidGenerationRequest, match="仅支持"):
        validated_submission(_submission("image21"))
