import json

from pydantic import PrivateAttr
from trulens.providers.openai import OpenAI

from ..core import user_prompt


class NativeJudge(OpenAI):
    _transport = PrivateAttr()

    def __init__(self, transport):
        super().__init__(model_engine=transport.name)
        self._transport = transport

    def _create_chat_completion(self, prompt=None, messages=None, response_format=None, **kwargs):
        # Native TruLens prompt + schema, transported through Responses.
        # Chat Completions-only and unsupported generation arguments are omitted.
        content = messages if messages is not None else prompt
        if content is None:
            raise ValueError("TruLens supplied no judge prompt")
        return self._transport.respond(content, response_format)


def score_case(case, output, judge, threshold):
    value, reasons = NativeJudge(judge).relevance_with_cot_reasons(prompt=user_prompt(case), response=output)
    return value, json.dumps(reasons, ensure_ascii=False, default=str)
