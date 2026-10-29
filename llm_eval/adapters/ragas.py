from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import Faithfulness


class NativeJudge(InstructorBaseRagasLLM):
    def __init__(self, transport):
        self.transport = transport

    def generate(self, prompt, response_model, **kwargs):
        return self.transport.respond(prompt, response_model)

    async def agenerate(self, prompt, response_model, **kwargs):
        return await self.transport.arespond(prompt, response_model)


def score_case(case, output, judge, threshold):
    result = Faithfulness(llm=NativeJudge(judge)).score(
        user_input=case["prompt"], response=output, retrieved_contexts=case["contexts"])
    return result.value, getattr(result, "reason", None) or "Native Ragas claim entailment"
