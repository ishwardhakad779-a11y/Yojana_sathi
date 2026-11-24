import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import db
from app.agent.graph import build_graph
from app.rag import retriever

# Configure structured logging
class RequestIdFilter(logging.Filter):
    """Inject request_id into every log record, defaulting to 'none'."""
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "none"
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s",
)
logging.getLogger().addFilter(RequestIdFilter())
logger = logging.getLogger("yojana_sathi.api")

# Context variable for request correlation
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="none")

UI_FILE = Path(__file__).parent.parent / "ui" / "index.html"

ADMIN_UI_FILE = Path(__file__).parent.parent / "ui" / "admin.html"

# Rate limiter: default 20 requests per minute per IP
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing YojanaSaathi services...")
    db.init_db()
    yield
    logger.info("Shutting down YojanaSaathi services...")
    db.close_db()


app = FastAPI(
    title="YojanaSaathi",
    description="AI Agent for Indian Government Schemes in Hinglish",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware with correlation IDs
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Add request correlation ID and log all requests with timing."""
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request_id_ctx.set(req_id)

    # Inject request_id into logging context
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = request_id_ctx.get()
        return record

    logging.setLogRecordFactory(record_factory)

    start_time = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start_time) * 1000, 2)

    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} - {duration_ms}ms"
    )

    response.headers["X-Request-ID"] = req_id
    return response


agent = build_graph()


class Question(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=600,
        description="User question in Hinglish, Hindi, or English",
    )
    session_id: str = Field(
        default="default",
        max_length=64,
        description="Unique session identifier for multi-turn conversations",
    )

    is_voice: bool = Field(default=False, description="Whether the question was asked via voice")

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Sawal khaali nahi ho sakta.")
        return clean

    @field_validator("session_id")
    @classmethod
    def validate_session(cls, v: str) -> str:
        # Sanitize session_id to alphanumeric and dashes
        clean = "".join(c for c in v if c.isalnum() or c in ("-", "_")).strip()
        return clean or "default"


@app.get("/")
def home():
    if not UI_FILE.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(UI_FILE)


@app.get("/admin")
def admin():
    if not ADMIN_UI_FILE.exists():
        raise HTTPException(status_code=404, detail="Admin UI file not found")
    return FileResponse(ADMIN_UI_FILE)


class AdminLogin(BaseModel):
    username: str
    password: str


@app.post("/admin/login")
def admin_login(req: AdminLogin):
    """Simple admin login - in production use proper auth"""
    admin_user = os.getenv("ADMIN_USER", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123")

    if req.username == admin_user and req.password == admin_pass:
        return {"success": True, "message": "Login successful"}
    return {"success": False, "message": "Invalid credentials"}


@app.get("/health")
def health():
    return {"status": "YojanaSaathi chal raha hai"}


@app.get("/healthz")
def deep_health():
    db_ok = db.check_health()
    rag_ok = retriever.check_health()
    all_ok = db_ok and rag_ok

    status_code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if all_ok else "unhealthy",
            "database": "connected" if db_ok else "error",
            "vector_store": "ready" if rag_ok else "error",
        },
    )


@app.get("/history/{session_id}")
def history(session_id: str):
    clean_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")).strip() or "default"
    return {"session_id": clean_id, "messages": db.get_history(clean_id)}


@app.post("/ask")
@limiter.limit("25/minute")
def ask_endpoint(request: Request, q: Question):
    try:
        prev = db.get_session(q.session_id)
        result = agent.invoke(
            {
                "question": q.question,
                "profile": prev.get("profile", {}),
                "intent": prev.get("intent", "info"),
                "topic": prev.get("topic", ""),
            }
        )

        db.save_session(
            q.session_id,
            result["profile"],
            result["intent"],
            result.get("topic", ""),
        )
        db.save_message(q.session_id, "user", q.question, is_voice=q.is_voice)
        db.save_message(
            q.session_id, "assistant", result["reply"], result.get("sources", [])
        )

        return {
            "answer": result["reply"],
            "profile": result["profile"],
            "sources": result.get("sources", []),
        }

    except Exception as e:
        logger.error(f"Error processing question for session '{q.session_id}': {e}", exc_info=True)
        # Return friendly fallback response instead of breaking client
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "answer": "Maaf kijiye, abhi server par thoda load hai ya token limit aayi hai. Kripya 30 second baad dobara poochhein.",
                "profile": prev.get("profile", {}) if "prev" in locals() else {},
                "sources": [],
                "error": True,
            },
        )


@app.get("/admin/stats")
def admin_stats(password: str):
    """
    Admin dashboard statistics endpoint.

    Args:
        password: Simple password protection (check via query param)

    Returns:
        JSON with user stats, top schemes, recent questions
    """
    # Password check - must be set in environment
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with db._conn() as conn:
            cursor = conn.cursor()

            # Total users
            cursor.execute("SELECT COUNT(DISTINCT session_id) FROM sessions")
            total_users = cursor.fetchone()[0] or 0

            # Total questions
            cursor.execute("SELECT COUNT(*) FROM messages WHERE role = 'user'")
            total_questions = cursor.fetchone()[0] or 0

            # Today's users (last 24 hours)
            cursor.execute("""
                SELECT COUNT(DISTINCT session_id)
                FROM messages
                WHERE role = 'user'
                AND datetime(timestamp) > datetime('now', '-1 day')
            """)
            today_users = cursor.fetchone()[0] or 0

            # Voice interaction stats
            cursor.execute("SELECT COUNT(*) FROM messages WHERE role = 'user' AND is_voice = 1")
            total_voice_questions = cursor.fetchone()[0] or 0

            # Average questions per user
            avg_questions = round(total_questions / total_users, 1) if total_users > 0 else 0

            # Top schemes from sources
            cursor.execute("""
                SELECT sources
                FROM messages
                WHERE role = 'assistant' AND sources IS NOT NULL AND sources != '[]'
                LIMIT 1000
            """)

            scheme_counts = {}
            for row in cursor.fetchall():
                try:
                    sources = json.loads(row[0])
                    for source in sources:
                        name = source.get("name", "Unknown")
                        scheme_counts[name] = scheme_counts.get(name, 0) + 1
                except:
                    pass

            top_schemes = sorted(scheme_counts.items(), key=lambda x: x[1], reverse=True)[:10]

            # Recent questions (last 20)
            cursor.execute("""
                SELECT m.session_id, m.content, m.created_at, s.profile, m.is_voice
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.session_id
                WHERE m.role = 'user'
                ORDER BY m.id DESC
                LIMIT 20
            """)

            recent_questions = []
            for row in cursor.fetchall():
                session_id, content, created_at, profile_json, is_voice = row
                try:
                    profile = json.loads(profile_json) if profile_json else {}
                except:
                    profile = {}

                recent_questions.append({
                    "session_id": session_id[:8],  # Short ID for privacy
                    "question": content[:100],  # Truncate long questions
                    "timestamp": str(created_at),
                    "is_voice": bool(is_voice),
                    "profile": {
                        "age": profile.get("age"),
                        "occupation": profile.get("occupation")
                    }
                })

            return {
                "overview": {
                    "total_users": total_users,
                    "total_questions": total_questions,
                    "today_users": today_users,
                    "total_voice_questions": total_voice_questions,
                    "avg_questions_per_user": avg_questions
                },
                "top_schemes": [
                    {"name": name, "count": count, "percentage": round(count/total_questions*100, 1) if total_questions > 0 else 0}
                    for name, count in top_schemes
                ],
                "recent_questions": recent_questions,
                "timestamp": time.time()
            }
    except Exception as e:
        logger.error(f"Admin stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch stats")
