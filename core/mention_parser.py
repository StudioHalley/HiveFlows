"""@ mention parser for multi-agent chat routing (Phase 4 Bloc M).

Parses messages for @agent-slug patterns and determines routing:
- @slug found → route to that agent only
- No @ + 1 agent in workspace → implicit 1:1 routing (use default)
- No @ + N agents → return system message asking user to @mention
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Matches @slug where slug is alphanumeric + hyphens/underscores/dots
# Must be at word boundary (start of string or after whitespace/punctuation)
_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-zA-Z0-9][a-zA-Z0-9._-]*)")


@dataclass
class MentionResult:
    """Result of parsing a message for @mentions."""
    mentioned_ids: list[str]       # All @slugs found in message
    resolved_agent_id: str | None  # The agent to route to (None = show help)
    system_message: str | None     # System message to show instead of routing
    clean_message: str             # Message with @mentions stripped for the agent


def parse_mentions(
    message: str,
    available_agent_ids: list[str],
    default_agent_id: str | None = None,
) -> MentionResult:
    """Parse @mentions from a chat message and resolve routing.

    Args:
        message: The user's raw message
        available_agent_ids: Agent IDs available in the current workspace
        default_agent_id: The agent_id from the request (agent selector or explicit)

    Returns:
        MentionResult with routing decision
    """
    mentions = _MENTION_RE.findall(message)
    # Deduplicate while preserving order
    seen = set()
    unique_mentions = []
    for m in mentions:
        m_lower = m.lower()
        if m_lower not in seen:
            seen.add(m_lower)
            unique_mentions.append(m)

    # Resolve mentions against available agents (case-insensitive)
    agent_map = {aid.lower(): aid for aid in available_agent_ids}
    resolved = [agent_map[m.lower()] for m in unique_mentions if m.lower() in agent_map]

    # Strip @mentions from message for the agent
    clean = _MENTION_RE.sub("", message).strip()
    # Collapse multiple spaces
    clean = re.sub(r"\s{2,}", " ", clean)

    if resolved:
        # Route to the first mentioned agent
        return MentionResult(
            mentioned_ids=resolved,
            resolved_agent_id=resolved[0],
            system_message=None,
            clean_message=clean or message,
        )

    # No valid @mention found
    if len(available_agent_ids) <= 1:
        # 1:1 mode — implicit routing to default or only agent
        target = default_agent_id or (available_agent_ids[0] if available_agent_ids else None)
        return MentionResult(
            mentioned_ids=[],
            resolved_agent_id=target,
            system_message=None,
            clean_message=message,
        )

    # Multiple agents, no @mention — use the explicitly selected agent
    if default_agent_id and default_agent_id in available_agent_ids:
        return MentionResult(
            mentioned_ids=[],
            resolved_agent_id=default_agent_id,
            system_message=None,
            clean_message=message,
        )

    # No valid agent selected either
    agent_list = ", ".join(f"@{aid}" for aid in available_agent_ids[:5])
    return MentionResult(
        mentioned_ids=[],
        resolved_agent_id=None,
        system_message=f"Plusieurs agents disponibles. Mentionnez un agent : {agent_list}",
        clean_message=message,
    )
