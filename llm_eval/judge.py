from __future__ import annotations

import asyncio
from typing import Any

from openai import OpenAI


class ResponsesJudge:
    """Transport adapter; framework prompts, schemas, scoring, and normalization stay native."""

    def __init__(self, config: dict):
        self.name = config["judge_model"]
        self.effort = config["judge_reasoning_effort"]
        self.max_tokens = config["judge_max_output_tokens"]
        self.client = OpenAI(timeout=config["timeout_seconds"], max_retries=2)
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def respond(self, prompt: str | list[dict], schema: Any = None):
        kwargs = dict(model=self.name, input=prompt, reasoning={"effort": self.effort},
                      max_output_tokens=self.max_tokens, store=False)
        self.usage["requests"] += 1
        if schema is None:
            response = self.client.responses.create(**kwargs)
        else:
            response = self.client.responses.parse(**kwargs, text_format=schema)
        if response.usage is not None:
            self.usage["input_tokens"] += response.usage.input_tokens
            self.usage["output_tokens"] += response.usage.output_tokens
        if response.status != "completed":
            raise RuntimeError("Judge response incomplete")
        if schema is not None:
            if response.output_parsed is None:
                raise ValueError("Judge response did not satisfy framework schema")
            return response.output_parsed
        if not response.output_text.strip():
            raise ValueError("Judge returned empty response")
        return response.output_text

    async def arespond(self, prompt, schema=None):
        return await asyncio.to_thread(self.respond, prompt, schema)

    def close(self):
        self.client.close()
