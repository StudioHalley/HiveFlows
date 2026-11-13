"""OAuth2 routes — Google + GitHub (Phase 4 Bloc H).

Flow:
  1. GET /auth/oauth/{provider}          → redirect to provider
  2. GET /auth/oauth/{provider}/callback  → exchange code, create/link user, redirect to dashboard

Desactivable: if OAUTH_*_CLIENT_ID is empty, the routes return 404.
"""

import logging

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware  # noqa: used in main.py

from api.routes_auth import create_personal_workspace
from audit import audit_log, get_client_ip
from auth_service import create_access_token
from config import settings
from database import get_db
from models.user import User

logger = logging.getLogger("hiveflows.oauth")

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])

_COOKIE_NAME = "hf_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# ── OAuth registry ───────────────────────────────────────────────────────────

oauth = OAuth()

if settings.oauth_google_enabled:
    oauth.register(
        name="google",
        client_id=settings.oauth_google_client_id,
        client_secret=settings.oauth_google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

if settings.oauth_github_enabled:
    oauth.register(
        name="github",
        client_id=settings.oauth_github_client_id,
        client_secret=settings.oauth_github_client_secret,
        authorize_url="https://github.com/login/oauth/authorize",
        access_token_url="https://github.com/login/oauth/access_token",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "user:email"},
    )

# ── Provider config ─────────────────────────────────────────────────────────

PROVIDERS = {
    "google": {
        "enabled": settings.oauth_google_enabled,
        "display_name": "Google",
    },
    "github": {
        "enabled": settings.oauth_github_enabled,
        "display_name": "GitHub",
    },
}


def _check_provider(provider: str):
    """Raise 404 if provider is unknown or disabled."""
    info = PROVIDERS.get(provider)
    if not info or not info["enabled"]:
        raise HTTPException(404, f"OAuth provider '{provider}' is not configured.")


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/{provider}")
async def oauth_redirect(provider: str, request: Request):
    """Step 1: redirect user to the OAuth provider's authorization page."""
    _check_provider(provider)
    client = oauth.create_client(provider)
    redirect_uri = f"{settings.oauth_redirect_base_url}/auth/oauth/{provider}/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 2: exchange code for token, fetch profile, create/link user."""
    _check_provider(provider)
    client = oauth.create_client(provider)

    try:
        token = await client.authorize_access_token(request)
    except Exception as e:
        logger.error("OAuth token exchange failed for %s: %s", provider, e)
        raise HTTPException(400, f"OAuth authentication failed: {e}")

    # ── Fetch user info ──────────────────────────────────────────────
    if provider == "google":
        user_info = token.get("userinfo")
        if not user_info:
            user_info = await client.userinfo(token=token)
        email = user_info.get("email")
        display_name = user_info.get("name", email.split("@")[0])
        oauth_id = user_info.get("sub")  # Google unique ID

    elif provider == "github":
        resp = await client.get("user", token=token)
        profile = resp.json()
        oauth_id = str(profile.get("id"))
        display_name = profile.get("name") or profile.get("login", "")

        # GitHub may not return email in profile — fetch from /user/emails
        email = profile.get("email")
        if not email:
            emails_resp = await client.get("user/emails", token=token)
            emails = emails_resp.json()
            primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
            if primary:
                email = primary["email"]
            elif emails:
                email = emails[0]["email"]

    else:
        raise HTTPException(400, f"Unsupported provider: {provider}")

    if not email:
        raise HTTPException(400, "Could not retrieve email from OAuth provider.")

    # ── Find or create user ──────────────────────────────────────────
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        # Link OAuth to existing account (if not already linked)
        if not user.oauth_provider:
            user.oauth_provider = provider
            user.oauth_id = oauth_id
        elif user.oauth_provider == provider and user.oauth_id != oauth_id:
            # Same provider but different OAuth ID — update
            user.oauth_id = oauth_id

        if not user.is_active:
            raise HTTPException(403, "Account deactivated.")

        await audit_log(db, action="auth.oauth_login", user_id=user.id,
                        resource_type="user", resource_id=str(user.id),
                        details={"provider": provider, "email": email},
                        ip=get_client_ip(request))
        await db.commit()
    else:
        # Create new user from OAuth
        user_slug = email.split("@")[0].lower().replace(" ", "-").replace(".", "-")
        slug_check = await db.execute(select(User).where(User.slug == user_slug))
        if slug_check.scalar_one_or_none():
            import uuid as _uuid
            user_slug = f"{user_slug}-{_uuid.uuid4().hex[:8]}"
        user = User(
            email=email,
            slug=user_slug,
            display_name=display_name,
            hashed_password=None,  # OAuth-only, no password
            oauth_provider=provider,
            oauth_id=oauth_id,
        )
        db.add(user)
        await db.flush()

        ws = await create_personal_workspace(db, user)

        await audit_log(db, action="auth.oauth_register", user_id=user.id,
                        workspace_id=ws.id,
                        resource_type="user", resource_id=str(user.id),
                        details={"provider": provider, "email": email},
                        ip=get_client_ip(request))
        await db.commit()
        await db.refresh(user)

    # ── Issue JWT and redirect to dashboard ──────────────────────────
    access_token = create_access_token(user.id)
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(_COOKIE_NAME, access_token, max_age=_COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=settings.is_production)
    return resp


# ── Helper for templates ─────────────────────────────────────────────────────

def get_enabled_providers() -> list[dict]:
    """Return list of enabled OAuth providers (for login page buttons)."""
    return [
        {"name": name, "display_name": info["display_name"]}
        for name, info in PROVIDERS.items()
        if info["enabled"]
    ]
