"""Local accounts and revocable sessions for private archives."""

from contextlib import closing
from hashlib import scrypt, sha256
from hmac import compare_digest
import secrets
import sqlite3
from time import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from .model_setup import DATA_DIR

router = APIRouter(prefix="/api/account")
COOKIE = "echo_session"
SESSION_SECONDS = 7 * 24 * 60 * 60


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY NOT NULL, username TEXT UNIQUE NOT NULL,
                salt BLOB NOT NULL, verifier BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                digest BLOB PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                expires INTEGER NOT NULL
            );
        """)


def connect():
    return sqlite3.connect(DATA_DIR / "accounts.sqlite3", timeout=10)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=256)


def password_hash(password, salt):
    return scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024**2)


def session_digest(request):
    token = request.cookies.get(COOKIE, "")
    return sha256(token.encode()).digest()


def issue_session(db, user_id, request, response):
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires <= ? OR digest=?", (int(time()), session_digest(request)))
    db.execute("INSERT INTO sessions VALUES (?,?,?)",
               (sha256(token.encode()).digest(), user_id, int(time()) + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        path="/api", max_age=SESSION_SECONDS)


def session_user(request: Request):
    with closing(connect()) as db:
        return db.execute("""
            SELECT u.id, u.username FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE s.digest=? AND s.expires>?
        """, (session_digest(request), int(time()))).fetchone()


def require_user(request: Request, user=Depends(session_user)):
    if user is None or request.headers.get("x-echo-account", user[1]) != user[1]:
        raise HTTPException(401, "Sign in to your ECHO account.")
    return user[0]


@router.get("")
def account(user=Depends(session_user)):
    return {"username": user[1] if user else None}


@router.post("/register", status_code=201)
def register(data: Credentials, request: Request, response: Response):
    username = data.username.lower()
    password = data.password.get_secret_value()
    if len(password) < 12:
        raise HTTPException(422, "Choose a password with at least 12 characters.")
    salt = secrets.token_bytes(16)
    verifier = password_hash(password, salt)
    user_id = secrets.token_hex(16)
    with closing(connect()) as db, db:
        try:
            db.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, username, salt, verifier))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is taken.") from None
        issue_session(db, user_id, request, response)
    return {"username": username}


@router.post("/login")
def login(data: Credentials, request: Request, response: Response):
    username = data.username.lower()
    with closing(connect()) as db, db:
        user = db.execute("SELECT id,salt,verifier FROM users WHERE username=?", (username,)).fetchone()
        verifier = password_hash(data.password.get_secret_value(), user[1] if user else bytes(16))
        if not user or not compare_digest(verifier, user[2]):
            raise HTTPException(401, "Incorrect username or password.")
        issue_session(db, user[0], request, response)
    return {"username": username}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    with closing(connect()) as db, db:
        db.execute("DELETE FROM sessions WHERE digest=?", (session_digest(request),))
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict")
