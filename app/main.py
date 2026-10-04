from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.agent.graph import build_graph

UI_FILE = Path(__file__).parent.parent / "ui" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="YojanaSaathi", lifespan=lifespan)
agent = build_graph()


class Question(BaseModel):
    question: str
    session_id: str = "default"


@app.get("/")
def home():
    return FileResponse(UI_FILE)


@app.get("/health")
def health():
    return {"status": "YojanaSaathi chal raha hai"}


@app.get("/history/{session_id}")
def history(session_id: str):
    return {"session_id": session_id, "messages": db.get_history(session_id)}


@app.post("/ask")
def ask_endpoint(q: Question):
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