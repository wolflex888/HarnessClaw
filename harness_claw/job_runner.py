from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Awaitable

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.providers.anthropic import AnthropicProvider
from harness_claw.providers.base import BaseProvider
from harness_claw.session import Session

PROVIDERS: dict[str, BaseProvider] = {
    "anthropic": AnthropicProvider(),
}


class JobRunner:
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self._sessions: dict[str, Session] = {}  # keyed by agent_id

    def get_or_create_session(self, agent_id: str) -> Session:
        if agent_id not in self._sessions:
            agent = self._registry.get(agent_id)
            self._sessions[agent_id] = Session(agent_id=agent_id, model=agent.model)
        return self._sessions[agent_id]

    def get_session(self, agent_id: str) -> Session | None:
        return self._sessions.get(agent_id)

    async def run_job(
        self,
        agent_id: str,
        text: str,
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        job_id = str(uuid.uuid4())
        agent = self._registry.get(agent_id)
        session = self.get_or_create_session(agent_id)
        provider = PROVIDERS[agent.provider]

        session.add_user_message(text)
        await send({
            "type": "job_update",
            "job_id": job_id,
            "agent_id": agent_id,
            "title": text[:60],
            "status": "running",
            "progress": None,
        })

        assistant_text = ""

        try:
            if agent.orchestrates:
                tools = [self._make_call_agent_tool(agent.orchestrates)]

                async def tool_executor(tool_name: str, tool_input: dict[str, Any]) -> str:
                    if tool_name != "call_agent":
                        return f"Error: unknown tool '{tool_name}'"
                    sub_agent_id = tool_input["agent_id"]
                    prompt = tool_input["prompt"]
                    return await self._run_sub_agent(sub_agent_id, prompt, send)

                async for event in provider.stream_with_tools(
                    session.messages, agent.system_prompt, agent.model,
                    tools, tool_executor, agent.max_tokens,
                ):
                    if event["type"] == "token":
                        assistant_text += event["delta"]
                        await send({"type": "token", "job_id": job_id, "delta": event["delta"]})
                    elif event["type"] == "tool_call":
                        await send({"type": "tool_call", "job_id": job_id, **event})
                    elif event["type"] == "usage":
                        session.input_tokens += event["input_tokens"]
                        session.output_tokens += event["output_tokens"]
                        await send({
                            "type": "usage",
                            "job_id": job_id,
                            "input_tokens": session.input_tokens,
                            "output_tokens": session.output_tokens,
                            "cost_usd": session.cost_usd,
                        })
            else:
                async for event in provider.stream_chat(
                    session.messages, agent.system_prompt, agent.model, agent.max_tokens,
                ):
                    if event["type"] == "token":
                        assistant_text += event["delta"]
                        await send({"type": "token", "job_id": job_id, "delta": event["delta"]})
                    elif event["type"] == "usage":
                        session.input_tokens += event["input_tokens"]
                        session.output_tokens += event["output_tokens"]
                        await send({
                            "type": "usage",
                            "job_id": job_id,
                            "input_tokens": session.input_tokens,
                            "output_tokens": session.output_tokens,
                            "cost_usd": session.cost_usd,
                        })

            session.add_assistant_message(assistant_text)
            await send({
                "type": "job_update",
                "job_id": job_id,
                "agent_id": agent_id,
                "status": "completed",
                "progress": None,
            })

        except Exception as exc:
            await send({"type": "error", "job_id": job_id, "message": str(exc)})
            await send({
                "type": "job_update",
                "job_id": job_id,
                "agent_id": agent_id,
                "status": "failed",
                "progress": None,
            })

        return job_id

    async def _run_sub_agent(
        self,
        agent_id: str,
        prompt: str,
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        sub_job_id = str(uuid.uuid4())
        agent = self._registry.get(agent_id)
        provider = PROVIDERS[agent.provider]

        # Sub-agents run with a fresh session per invocation (no history)
        sub_session = Session(agent_id=agent_id, model=agent.model)
        sub_session.add_user_message(prompt)

        await send({
            "type": "job_update",
            "job_id": sub_job_id,
            "agent_id": agent_id,
            "title": prompt[:60],
            "status": "running",
            "progress": None,
        })

        result_text = ""
        async for event in provider.stream_chat(
            sub_session.messages, agent.system_prompt, agent.model, agent.max_tokens,
        ):
            if event["type"] == "token":
                result_text += event["delta"]
                await send({"type": "token", "job_id": sub_job_id, "delta": event["delta"]})
            elif event["type"] == "usage":
                await send({"type": "usage", "job_id": sub_job_id, **event, "cost_usd": 0.0})

        await send({
            "type": "job_update",
            "job_id": sub_job_id,
            "agent_id": agent_id,
            "status": "completed",
            "progress": None,
        })

        return result_text

    @staticmethod
    def _make_call_agent_tool(orchestrates: list[str]) -> dict[str, Any]:
        return {
            "name": "call_agent",
            "description": (
                "Call a sub-agent to perform a specific task. "
                "Returns the agent's full response as a string."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The ID of the sub-agent to call.",
                        "enum": orchestrates,
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task or question to send to the sub-agent.",
                    },
                },
                "required": ["agent_id", "prompt"],
            },
        }
