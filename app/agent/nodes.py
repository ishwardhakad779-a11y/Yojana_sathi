import os
import json
from typing import TypedDict
from dotenv import load_dotenv
from groq import Groq

from app.rag.retriever import search

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL ="openai/gpt-oss-20b"

REQUIRED = {
    "age": "aapki umar kitni hai",
    "occupation": "aap kya kaam karte hain (kisan, student, dukaandar, mazdoor, ...)",
    "annual_income": "parivaar ki saalana aay kitni hai (rupaye mein)",
}


class State(TypedDict, total=False):
    question: str
    topic: str
    profile: dict
    intent: str
    docs: list
    reply: str
    sources: list


def missing(profile: dict):
    return [k for k in REQUIRED if profile.get(k) in (None, "")]


def extract_profile(state: State):
    old = state.get("profile", {})
    prompt = f"""User ka message padho aur sirf JSON do.
Keys:
- age (number ya null)
- occupation (string ya null)
- annual_income (rupaye mein number, jaise 1.5 lakh = 150000, ya null)
- state (string ya null)
- gender (string ya null)
- intent ("greeting" agar message sirf hello/hi/hey jaisa abhivadan hai, "eligibility" agar user poochh raha hai ki use kaunsi yojana milegi, warna "info")

Purani profile: {json.dumps(old, ensure_ascii=False)}
Message: {state['question']}
Jo cheez message mein nahi hai, usse null rakho."""

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    intent = data.pop("intent", "info")
    profile = {**old, **{k: v for k, v in data.items() if v is not None}}

    # pichli baar eligibility puchh rahe the aur info abhi bhi adhoori hai
    if (
        state.get("intent") == "eligibility"
        and missing(profile)
        and intent != "greeting"
    ):
        intent = "eligibility"

    return {"profile": profile, "intent": intent}


def route(state: State):
    if state["intent"] == "greeting":
        return "greet"
    if state["intent"] == "eligibility" and missing(state["profile"]):
        return "ask"
    return "retrieve"


def greet(state: State):
    return {
        "reply": (
            "Namaste! Main YojanaSaathi hoon. Aap mujhse kisi sarkari yojana ke baare mein "
            "pooch sakte hain, ya bol sakte hain \"mujhe kaunsi yojana milegi?\" aur main "
            "aapse thodi jaankari lekar sahi yojana bataunga."
        ),
        "sources": [],
    }


def ask(state: State):
    field = missing(state["profile"])[0]
    return {
        "reply": f"Aapke liye sahi yojana dhoondhne ke liye mujhe thodi jaankari chahiye. Bataiye, {REQUIRED[field]}?",
        "sources": [],
        "topic": state.get("topic") or state["question"],
    }


def build_query(topic: str, profile: dict) -> str:
    """Hinglish sawal ko chhoti English search query mein badalta hai."""
    prompt = f"""Neeche user ka sawal aur profile hai. Isse ek chhoti ENGLISH search query banao
(max 12 words) jo sarkari yojana dhoondhne ke kaam aaye. Hinglish shabdon ko English mein badlo
(jaise rehri/thela/patri = street vendor, kisan = farmer, beti = girl child,
mazdoor = labourer, dukaan = small shop business, padhai = education scholarship).
Sirf query likho, aur kuch nahi.

Sawal: {topic}
Profile: {json.dumps(profile, ensure_ascii=False)}"""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return topic


def retrieve(state: State):
    profile = state.get("profile", {})
    topic = state.get("topic") or state["question"]
    query = build_query(topic, profile)

    docs = search(query, k=4)
    seen = {d["name"] for d in docs}
    for d in search(topic, k=4):  # asli Hinglish sawal se bhi
        if d["name"] not in seen:
            docs.append(d)
            seen.add(d["name"])
    docs = docs[:6]

    print("SEARCH QUERY:", query)
    print("RETRIEVED:", [d["name"] for d in docs])
    return {"docs": docs}


def answer(state: State):
    docs = state["docs"]
    topic = state.get("topic") or state["question"]
    context = "\n\n".join(f"[{i + 1}] {d['text']}" for i, d in enumerate(docs))

    prompt = f"""Tum YojanaSaathi ho. Hinglish mein, simple bhasha mein jawab do.
User ki profile: {json.dumps(state.get('profile', {}), ensure_ascii=False)}
User ka sawal: {topic}

Context (har yojana ke aage ek number hai):
{context}

Rules:
- Context mein se jitni yojanayein user ki profile aur sawal ke hisaab se sach mein kaam ki hain, sabka naam batao (maximum 3). Sirf ek par ruk mat jao agar doosri bhi relevant hain.
- Har yojana ke saath ek line mein batao ki kyun kaam ki hai aur kaise apply karna hai.
- Jo yojana relevant nahi hai, usse mat batao.
- Jawab ke text mein [1] jaise number mat likhna, sirf yojana ka naam likhna.
- Agar context mein jawab nahi hai, bolo "mujhe pakki jaankari nahi hai" aur "used" khaali rakho.
- End mein bolo ki apply karne se pehle official site pe rules check kar lein.

Sirf JSON do: {{"answer": "...", "used": [1, 3]}}
"used" mein sirf un yojanaon ke number likho jinhe tumne jawab mein bataya."""

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)

    sources, seen = [], set()
    for n in data.get("used", []):
        try:
            d = docs[int(n) - 1]
        except (ValueError, IndexError, TypeError):
            continue
        if d["name"] not in seen:
            seen.add(d["name"])
            sources.append({"name": d["name"], "url": d["url"]})

    return {"reply": data.get("answer", ""), "sources": sources, "topic": ""}