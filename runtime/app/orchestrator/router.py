"""Routes a free-text chat prompt to an agent slug.

Uses a cheap/small model when ANTHROPIC_API_KEY is set. Falls back to
keyword matching against agent slugs/descriptions otherwise, so chat works
out of the box without an API key.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import settings

# Excluded from matching so common connector words don't spuriously tie
# real matches — e.g. "to" is a substring of "post-deploy" and "push to
# registry" alike, which used to make unrelated agents outscore the agent
# actually named in the prompt.
_STOPWORDS = {
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "this", "that", "it", "with", "from", "by", "as", "please", "me", "my",
}
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def _stem(word: str) -> str:
    # Just enough to line up "tests"/"test", "runs"/"run" — not a real
    # stemmer, so it's guarded to avoid mangling short words or doubles
    # like "class".
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokenize(text: str) -> set[str]:
    words = set(_WORD_RE.findall(text.lower())) - _STOPWORDS
    return {_stem(w) for w in words}


@dataclass
class RouteMatch:
    agent_slug: str
    params: dict[str, Any]


async def classify(prompt: str, agents: list[dict[str, Any]]) -> RouteMatch:
    if settings.anthropic_api_key:
        return await _classify_with_claude(prompt, agents)
    return _classify_by_keyword(prompt, agents)


def _classify_by_keyword(prompt: str, agents: list[dict[str, Any]]) -> RouteMatch:
    prompt_tokens = _tokenize(prompt)
    best = None
    best_score = 0
    for agent in agents:
        haystack_tokens = _tokenize(f"{agent['slug']} {agent['name']} {agent['description']}")
        score = len(prompt_tokens & haystack_tokens)
        # An exact mention of the agent's own slug is a much stronger signal
        # than an incidental word overlap in its description — e.g. "deploy"
        # in the prompt should beat "verify" merely describing itself as
        # running "post-deploy".
        if agent["slug"] in prompt_tokens:
            score += 3
        if score > best_score:
            best_score = score
            best = agent
    if best is None:
        best = agents[0] if agents else None
    if best is None:
        raise ValueError("No agents registered to route to")
    return RouteMatch(agent_slug=best["slug"], params={"prompt": prompt})


async def _classify_with_claude(prompt: str, agents: list[dict[str, Any]]) -> RouteMatch:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    agent_list = "\n".join(f"- {a['slug']}: {a['description']}" for a in agents)

    response = await client.messages.create(
        model=settings.intent_router_model,
        max_tokens=200,
        system=(
            "You route a user's DevOps request to exactly one agent slug from the list below. "
            "Reply with ONLY a JSON object: {\"agent_slug\": \"...\", \"params\": {...}}.\n\n"
            f"Available agents:\n{agent_list}"
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        parsed = json.loads(text)
        return RouteMatch(agent_slug=parsed["agent_slug"], params=parsed.get("params", {}))
    except (json.JSONDecodeError, KeyError):
        return _classify_by_keyword(prompt, agents)
