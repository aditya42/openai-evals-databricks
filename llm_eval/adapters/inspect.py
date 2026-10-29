import asyncio

from inspect_ai.model import ChatMessageUser, ModelName, ModelOutput
from inspect_ai.scorer import CORRECT, Target, match
from inspect_ai.solver import TaskState

from ..core import evaluation_input


def score_case(case, output, judge, threshold):
    target = Target(case["inspect_target"])
    state = TaskState(
        model=ModelName("openai/" + case.get("model", "cached-response")),
        sample_id=case["id"], epoch=1, input=evaluation_input(case),
        messages=[ChatMessageUser(content=evaluation_input(case))], target=target,
        output=ModelOutput.from_content("cached-response", output),
    )
    result = asyncio.run(match(location="exact", ignore_case=False)(state, target))
    return (1.0 if result.value == CORRECT else 0.0), "Native Inspect exact-match scorer; surrounding whitespace ignored"
