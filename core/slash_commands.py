"""Slash command parser & handlers for chat (Phase 4 — Bloc N).

Parses messages starting with / and routes to the appropriate handler.
Commands are executed before the message reaches the LLM.
Results are returned as system messages in the chat.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Awaitable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_log
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from permissions import Permission, has_permission

logger = logging.getLogger("hiveflows.slash_commands")

# Matches /command at the start of a message, with optional arguments
_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)", re.DOTALL)


@dataclass
class SlashResult:
    """Result of parsing/executing a slash command."""
    is_command: bool           # True if the message was a slash command
    system_message: str | None  # Response to show in chat (None if not a command)


@dataclass
class CommandContext:
    """Context passed to slash command handlers."""
    user: User
    workspace_id: UUID
    member: WorkspaceMember
    db: AsyncSession
    raw_args: str  # Everything after the /command


# Type for command handlers
CommandHandler = Callable[[CommandContext], Awaitable[str]]

# ── Command Registry ────────────────────────────────────────────────────────

_COMMANDS: dict[str, CommandHandler] = {}
_COMMAND_HELP: dict[str, str] = {}


def register_command(name: str, help_text: str = ""):
    """Decorator to register a slash command handler."""
    def decorator(fn: CommandHandler) -> CommandHandler:
        _COMMANDS[name.lower()] = fn
        _COMMAND_HELP[name.lower()] = help_text
        return fn
    return decorator


# ── Parser ──────────────────────────────────────────────────────────────────

def is_slash_command(message: str) -> bool:
    """Check if a message starts with a slash command."""
    return bool(_SLASH_RE.match(message.strip()))


async def execute_slash_command(
    message: str,
    user: User,
    workspace_id: UUID | None,
    member: WorkspaceMember | None,
    db: AsyncSession,
) -> SlashResult:
    """Parse and execute a slash command.

    Returns SlashResult with is_command=False if the message is not a command.
    """
    match = _SLASH_RE.match(message.strip())
    if not match:
        return SlashResult(is_command=False, system_message=None)

    command_name = match.group(1).lower()
    raw_args = match.group(2).strip()

    handler = _COMMANDS.get(command_name)
    if not handler:
        available = ", ".join(f"/{c}" for c in sorted(_COMMANDS.keys()))
        return SlashResult(
            is_command=True,
            system_message=f"Commande inconnue : /{command_name}\nCommandes disponibles : {available}",
        )

    if not workspace_id or not member:
        return SlashResult(
            is_command=True,
            system_message="Les commandes slash nécessitent un workspace actif.",
        )

    ctx = CommandContext(
        user=user,
        workspace_id=workspace_id,
        member=member,
        db=db,
        raw_args=raw_args,
    )

    try:
        result_message = await handler(ctx)
        return SlashResult(is_command=True, system_message=result_message)
    except Exception as e:
        logger.exception(f"Slash command /{command_name} failed")
        return SlashResult(
            is_command=True,
            system_message=f"Erreur lors de l'exécution de /{command_name} : {str(e)}",
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_slug(args: str) -> str | None:
    """Extract a @slug or plain slug from args."""
    args = args.strip()
    if args.startswith("@"):
        args = args[1:]
    # Take only the first word
    slug = args.split()[0] if args else None
    return slug


async def _find_user_by_slug_or_email(db: AsyncSession, identifier: str) -> User | None:
    """Find a user by slug or email."""
    identifier = identifier.lstrip("@")
    result = await db.execute(
        select(User).where(
            (User.slug == identifier) | (User.email == identifier),
            User.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def _get_membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> WorkspaceMember | None:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


# ── Commands ────────────────────────────────────────────────────────────────

@register_command("invite", help_text="/invite @slug [role] — Inviter un utilisateur dans le workspace")
async def cmd_invite(ctx: CommandContext) -> str:
    """Invite a user to the current workspace."""
    if not has_permission(ctx.member.role, Permission.MEMBERS_MANAGE):
        return "Permission refusée. Seuls les admins et owners peuvent inviter."

    parts = ctx.raw_args.split()
    if not parts:
        return "Usage : /invite @slug [role]\nRôles possibles : member, admin, viewer"

    slug = parts[0].lstrip("@")
    role = parts[1] if len(parts) > 1 else "member"

    if role not in ("owner", "admin", "member", "viewer"):
        return f"Rôle invalide : {role}\nRôles possibles : member, admin, viewer"
    if role == "owner":
        return "Impossible d'inviter en tant qu'owner."

    target_user = await _find_user_by_slug_or_email(ctx.db, slug)
    if not target_user:
        return f"Utilisateur @{slug} introuvable."

    existing = await _get_membership(ctx.db, ctx.workspace_id, target_user.id)
    if existing:
        return f"@{slug} est déjà membre de ce workspace."

    wm = WorkspaceMember(workspace_id=ctx.workspace_id, user_id=target_user.id, role=role)
    ctx.db.add(wm)

    await audit_log(
        ctx.db, action="member.add", user_id=ctx.user.id,
        workspace_id=ctx.workspace_id, resource_type="member",
        resource_id=str(target_user.id),
        details={"slug": slug, "role": role, "via": "slash_command"},
    )
    await ctx.db.commit()

    display = target_user.display_name or slug
    return f"{display} (@{target_user.slug or target_user.email}) a été invité en tant que {role}."


@register_command("kick", help_text="/kick @slug — Retirer un utilisateur du workspace")
async def cmd_kick(ctx: CommandContext) -> str:
    """Remove a user from the current workspace."""
    if not has_permission(ctx.member.role, Permission.MEMBERS_MANAGE):
        return "Permission refusée. Seuls les admins et owners peuvent retirer des membres."

    slug = _parse_slug(ctx.raw_args)
    if not slug:
        return "Usage : /kick @slug"

    target_user = await _find_user_by_slug_or_email(ctx.db, slug)
    if not target_user:
        return f"Utilisateur @{slug} introuvable."

    target_membership = await _get_membership(ctx.db, ctx.workspace_id, target_user.id)
    if not target_membership:
        return f"@{slug} n'est pas membre de ce workspace."

    if target_membership.role == "owner":
        return "Impossible de retirer le propriétaire du workspace."

    await audit_log(
        ctx.db, action="member.remove", user_id=ctx.user.id,
        workspace_id=ctx.workspace_id, resource_type="member",
        resource_id=str(target_user.id),
        details={"slug": slug, "role": target_membership.role, "via": "slash_command"},
    )
    await ctx.db.delete(target_membership)
    await ctx.db.commit()

    display = target_user.display_name or slug
    return f"{display} (@{target_user.slug or target_user.email}) a été retiré du workspace."


@register_command("part", help_text="/part — Quitter le workspace actuel")
async def cmd_part(ctx: CommandContext) -> str:
    """Leave the current workspace."""
    if ctx.member.role == "owner":
        return "Le propriétaire ne peut pas quitter son workspace. Transférez la propriété d'abord."

    await audit_log(
        ctx.db, action="member.leave", user_id=ctx.user.id,
        workspace_id=ctx.workspace_id, resource_type="member",
        resource_id=str(ctx.user.id),
        details={"role": ctx.member.role, "via": "slash_command"},
    )
    await ctx.db.delete(ctx.member)
    await ctx.db.commit()

    return "Vous avez quitté le workspace."


@register_command("join", help_text="/join #workspace — Rejoindre un workspace (sur invitation)")
async def cmd_join(ctx: CommandContext) -> str:
    """Join a workspace by slug. Only works if an open invitation exists or workspace is the user's own."""
    slug = ctx.raw_args.strip().lstrip("#")
    if not slug:
        return "Usage : /join #workspace-slug"

    result = await ctx.db.execute(
        select(Workspace).where(Workspace.slug == slug, Workspace.is_active == True)
    )
    target_ws = result.scalar_one_or_none()
    if not target_ws:
        return f"Workspace #{slug} introuvable."

    existing = await _get_membership(ctx.db, target_ws.id, ctx.user.id)
    if existing:
        return f"Vous êtes déjà membre de #{slug}."

    # Only allow joining if the user is the workspace owner (personal workspace)
    # For other workspaces, they need an /invite from an admin
    if target_ws.owner_id == ctx.user.id:
        wm = WorkspaceMember(workspace_id=target_ws.id, user_id=ctx.user.id, role="owner")
        ctx.db.add(wm)
        await ctx.db.commit()
        return f"Vous avez rejoint #{slug}."

    return f"Vous devez être invité pour rejoindre #{slug}. Demandez à un admin d'utiliser /invite."


@register_command("members", help_text="/members — Lister les membres du workspace")
async def cmd_members(ctx: CommandContext) -> str:
    """List all members of the current workspace."""
    if not has_permission(ctx.member.role, Permission.CHAT_VIEW):
        return "Permission refusée."

    result = await ctx.db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ctx.workspace_id)
    )
    rows = result.all()
    if not rows:
        return "Aucun membre trouvé."

    lines = [f"Membres du workspace ({len(rows)}) :"]
    for wm, u in rows:
        slug_display = f"@{u.slug}" if u.slug else u.email
        lines.append(f"  {slug_display} — {u.display_name} ({wm.role})")
    return "\n".join(lines)


@register_command("help", help_text="/help — Afficher les commandes disponibles")
async def cmd_help(ctx: CommandContext) -> str:
    """Show available slash commands."""
    lines = ["Commandes disponibles :"]
    for name in sorted(_COMMAND_HELP.keys()):
        lines.append(f"  {_COMMAND_HELP[name]}")
    return "\n".join(lines)
