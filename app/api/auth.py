
import psycopg2
from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.middleware.auth import create_access_token, hash_password, verify_password
from app.middleware.rate_limiter import is_allowed_ip


router = APIRouter(tags=["auth"])

def _get_db_conn():
    return psycopg2.connect(settings.database_url)

@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, body: dict) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    allowed, _, _ = is_allowed_ip(
        client_ip,
        "/auth/register",
        limit=settings.auth_register_rate_limit_per_hour,
        window_seconds=3600,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")

    password_hash = hash_password(password)
    conn = _get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
            (username, password_hash),
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="User already exists") from None
    finally:
        cur.close()
        conn.close()

    token = create_access_token(username)
    return {"token": token}



@router.post("/auth/login")
async def login(request: Request, body: dict) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    allowed, _, _ = is_allowed_ip(
        client_ip,
        "/auth/login",
        limit=settings.auth_login_rate_limit_per_min,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")

    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT password_hash, is_admin FROM users WHERE username = %s",
        (username,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None or not verify_password(password, row[0]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(username, is_admin=bool(row[1]))
    return {"token": token}
