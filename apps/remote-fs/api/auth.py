from fastapi import Header, HTTPException
from config import cfg


async def verify_token(x_sync_token: str = Header(default="")) -> None:
    secret = cfg["sync"].get("secret", "")
    if secret and x_sync_token != secret:
        raise HTTPException(status_code=401, detail="Invalid sync token")
