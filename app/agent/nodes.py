import json
import logging
from typing import TypedDict
from dotenv import load_dotenv

from app.rag.retriever import search
from app.agent.llm import generate, generate_json, GROQ_MODELS

load_dotenv()
logger = logging.getLogger("yojana_sathi.nodes")
MODEL = GROQ_MODELS[0]

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
- occupation (string ya null; user jo kaam karta hai wo likho, jaise "kisan", "student", "street vendor", "mazdoor", "dukaandar", "painter", "driver", "carpenter", "tailor", etc.)
- annual_income (rupaye mein number, jaise 1.5 lakh = 150000, ya null)
- state (string ya null)
- gender (string ya null)
- intent ("greeting" agar message sirf hello/hi/hey jaisa abhivadan hai; "eligibility" sirf tab jab user general tarah se poochh raha hai ki use kaunsi yojana milegi aur koi khaas zaroorat ya yojana ka naam nahi bataya; "info" agar user kisi khaas yojana ke baare mein poochh raha hai ya kisi khaas zaroorat (ilaaj, ghar, loan, fasal bima, scholarship, pension, budhapa, bachat, khata, gas, rashan, toolkit, training) ke liye madad maang raha hai, ya koi general sawal poochh raha hai)

Purani profile: {json.dumps(old, ensure_ascii=False)}
Message: {state['question']}
Jo cheez message mein nahi hai, usse null rakho."""

    data = generate_json(prompt, temperature=0.0)
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


SCHEME_WORDS = [
    "stand-up", "standup", "mudra", "ayushman", "svanidhi", "kisan credit", "kcc",
    "fasal bima", "pmfby", "sukanya", "atal pension", "pension", "vishwakarma", "ujjwala",
    "awas", "jan dhan", "shram", "pm-kisan", "pm kisan", "scholarship", "nsp",
    "pmsby", "pmjjby", "suraksha bima", "jeevan jyoti", "matru vandana", "pmmvy",
    "maandhan", "maan-dhan", "pm-sym", "old age pension",
]


def route(state: State):
    if state["intent"] == "greeting":
        return "greet"

    q = state["question"].lower()
    names_a_scheme = any(w in q for w in SCHEME_WORDS)

    if state["intent"] == "eligibility" and missing(state["profile"]) and not names_a_scheme:
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
mazdoor = labourer, dukaan = small shop business, padhai = education scholarship,
budhapa/pension = old age pension APY, painter/rangai = unorganised painter).
Sirf query likho, aur kuch nahi.

Sawal: {topic}
Profile: {json.dumps(profile, ensure_ascii=False)}"""
    try:
        return generate(prompt, temperature=0.0)
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
- Agar context mein jawab nahi hai, ya sawal sarkari yojanaon ke baare mein nahi hai (jaise product/tractor/bike recommendation, crypto, share market, train ticket, weather, general gyan), toh context ko zabardasti connect mat karo. Bolo "mujhe pakki jaankari nahi hai" aur "used": [] rakho.
- End mein bolo ki apply karne se pehle official site pe rules check kar lein.

Sirf JSON do: {{"answer": "...", "used": [1, 3]}}
"used" mein sirf un yojanaon ke number likho jinhe tumne jawab mein bataya."""

    data = generate_json(prompt, temperature=0.2)

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