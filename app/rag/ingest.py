import json
from pathlib import Path
import chromadb

DATA = Path(__file__).parent.parent / "schemes" / "schemes.json"
DB_PATH = str(Path(__file__).parent.parent.parent / "chroma_db")

# Search behtar karne ke liye: har scheme ke English + Hinglish keywords
KEYWORDS = {
    "PM-Kisan Samman Nidhi": "farmer, kisan, agriculture, income support, Rs 6000 per year, cash transfer, small and marginal farmer, land holding",
    "Ayushman Bharat PM-JAY": "health insurance, free treatment, hospital, medical, illness, ilaaj, 5 lakh health cover, poor family",
    "PM Awas Yojana (PMAY)": "housing, house, home construction, pucca house, kaccha ghar, ghar, shelter, homeless",
    "PM Ujjwala Yojana": "LPG, gas cylinder, free gas connection, cooking gas, women, mahila, BPL household",
    "PM Mudra Yojana": "business loan, small business, shop, dukaan, self employed, micro enterprise, collateral free loan, shishu kishor tarun",
    "PM Fasal Bima Yojana (PMFBY)": "crop insurance, fasal bima, crop loss, fasal kharab, flood, drought, farmer, kisan, damage compensation",
    "Kisan Credit Card (KCC)": "kisan credit card, farm loan, agricultural credit, cheap loan for farmers, farmer, kisan, kheti ka loan, animal husbandry, fisheries",
    "Sukanya Samriddhi Yojana": "girl child, beti, daughter, savings scheme, bachat, education and marriage, post office account",
    "Atal Pension Yojana (APY)": "pension, old age, retirement, budhapa, unorganised sector worker, mazdoor, monthly pension at 60",
    "PM Vishwakarma Yojana": "artisan, craftsperson, carpenter, badhai, blacksmith, lohar, tailor, darzi, potter, barber, traditional trade, toolkit, skill training",
    "PM SVANidhi (Rehri-Patri Loan)": "street vendor, rehri, thela, patri, hawker, vegetable seller, sabzi, small loan, digital payment cashback, interest subsidy",
    "PM Jan Dhan Yojana": "bank account, zero balance account, no bank account, savings account, khata, RuPay card, overdraft, DBT, accident insurance",
    "e-Shram Card": "unorganised worker, labourer, mazdoor, construction worker, migrant worker, accident insurance, PMSBY, UAN, labour card",
    "National Scholarship Portal (NSP) Scholarships": "scholarship, student, education, fees, SC ST OBC minority, padhai, college, school, pre matric, post matric",
    "Stand-Up India": "new business loan, SC ST women entrepreneur, mahila udyami, greenfield enterprise, 10 lakh to 1 crore loan, startup",
}


def main():
    schemes = json.loads(DATA.read_text(encoding="utf-8"))
    client = chromadb.PersistentClient(path=DB_PATH)

    # purana index hata ke naya banao
    try:
        client.delete_collection("schemes")
    except Exception:
        pass
    col = client.get_or_create_collection("schemes")

    documents = []
    for s in schemes:
        kw = KEYWORDS.get(s["name"], "")
        documents.append(f"{s['name']}. Keywords: {kw}. {s['text']}")

    col.upsert(
        ids=[f"scheme_{i}" for i in range(len(schemes))],
        documents=documents,
        metadatas=[{"name": s["name"], "url": s["url"]} for s in schemes],
    )
    print(f"{len(schemes)} schemes load ho gayi.")


if __name__ == "__main__":
    main()