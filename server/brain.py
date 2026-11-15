"""Decides what to do with a "Hey Murmur" request.

One Claude call per request. Claude either calls the one tool it has, which texts the user's
computer agent (Instinct, over iMessage), with the request rewritten as a clear self-contained
message that carries the context it needs, or it answers directly when no action is needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("murmur.brain")

TOOL_NAME = "message_computer"

SYSTEM = """You are Murmur, the assistant inside a smart mouthpiece. The user whispers to you \
silently; a speech engine transcribes them, so expect small transcription mistakes. Only the user's \
requests reach you. You also get a recent transcript of what was said around them: their own \
lines ("Me"), other people nearby ("Person A", "Person B", ...), and your earlier replies ("Murmur").

You have one tool, message_computer. It texts the user's AI agent, which has its own computer in \
the cloud with a browser and the user's accounts. It can post on social media, send email and \
messages, browse, research, book and buy things, and take other actions online. It replies by text.

For each request:
- If it needs an action in the world, or information you do not have (anything current, personal \
or online), call message_computer. Write the message as the user, in the first person, addressed \
to their agent. Make it self-contained: fix transcription mistakes, and include the details from \
the transcript that the agent needs (names, places, times, what someone nearby said) without \
inventing anything. Keep the user's intent and wording where it matters, such as the text of a post.
- If it is a quick question you can answer from the transcript or general knowledge, answer it \
yourself without the tool.
- If it is not a real request (a stray or cut-off phrase), reply with a short question.

Your direct answers are shown on screen and read aloud, so keep them to one or two short spoken \
sentences, with no markdown, lists or emoji. Never mention tools, the agent, iMessage or Instinct."""

TOOL = {
    "name": TOOL_NAME,
    "description": "Text the user's cloud computer agent a request. It acts on it and replies by text.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The self-contained request, written as the user to their agent.",
            }
        },
        "required": ["message"],
        "additionalProperties": False,
    },
}


@dataclass
class Decision:
    kind: str  # "send": text the agent `text` | "answer": say `text` to the user
    text: str


class PassThroughBrain:
    """No LLM: every request goes to the agent as you said it. Used when no Anthropic key is set."""

    reason = "no ANTHROPIC_API_KEY, so requests are sent as you said them"

    async def decide(self, request: str, transcript: str) -> Decision:
        return Decision("send", request)


class ClaudeBrain:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", client=None) -> None:
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic()
        self.client = client
        self.model = model
        self.effort = effort

    async def decide(self, request: str, transcript: str) -> Decision:
        prompt = (
            f"<transcript>\n{transcript or '(nothing yet)'}\n</transcript>\n\n"
            f"<request>{request}</request>"
        )
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "auto"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
        return parse_response(response)


def parse_response(response) -> Decision:
    if getattr(response, "stop_reason", None) == "refusal":
        return Decision("answer", "Sorry, I can't help with that one.")
    texts = []
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "tool_use" and block.name == TOOL_NAME:
            message = (block.input or {}).get("message", "") if isinstance(block.input, dict) else ""
            if isinstance(message, str) and message.strip():
                return Decision("send", message.strip())
        elif kind == "text" and block.text.strip():
            texts.append(block.text.strip())
    return Decision("answer", " ".join(texts) or "Sorry, I didn't catch that.")


def make_brain(cfg):
    if not cfg.anthropic_key:
        return PassThroughBrain()
    return ClaudeBrain(model=cfg.llm_model)
