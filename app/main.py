import logging
import os
from contextlib import asynccontextmanager
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

# Configure production logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yojana_sathi.api")

UI_FILE = Path(__file__).parent.parent / "ui" / "index.html"

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
        db.save_message(q.session_id, "user", q.question)
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
