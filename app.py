"""
MarkScout - Flask backend
A preliminary trademark-risk screening tool with affiliate domain routing
and a premium name-generation upsell.

IMPORTANT: This tool provides an AUTOMATED, BEST-EFFORT, PRELIMINARY risk
screen. It is NOT legal advice and NOT a substitute for a professional
trademark clearance search or a licensed attorney. This is made explicit
in the API responses and the UI so end users are never told a name is
"safe" with more confidence than the underlying data supports.
"""

import re
import os
import csv
import random
from datetime import datetime, timezone
from urllib.parse import quote_plus
import difflib

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, session

app = Flask(__name__)

# SECRET_KEY signs the session cookie used for the free-search counter and
# the "unlocked" flag. The fallback below is fine for local testing only -
# set a real SECRET_KEY environment variable before deploying, or every
# server restart invalidates everyone's session (and a predictable key is
# a security risk for anything beyond this simple counter).
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-before-deploying")

# --------------------------------------------------------------------------
# CONFIG - put your own affiliate IDs here before deploying
# --------------------------------------------------------------------------
AFFILIATE_IDS = {
    "namecheap": "YOUR_NAMECHEAP_AFFILIATE_ID",
    "godaddy": "YOUR_GODADDY_AFFILIATE_ID",
    "hostinger": "YOUR_HOSTINGER_AFFILIATE_ID",
}

# Referral IDs for trademark filing / attorney services, shown when a
# search comes back RED or YELLOW - i.e. the moment someone actually needs
# professional help, not a generic ad slot.
REFERRAL_IDS = {
    "vakilsearch": "YOUR_VAKILSEARCH_AFFILIATE_ID",
    "legalraasta": "YOUR_LEGALRAASTA_AFFILIATE_ID",
    "indiafilings": "YOUR_INDIAFILINGS_AFFILIATE_ID",
    "trademarkia": "YOUR_TRADEMARKIA_AFFILIATE_ID",
    "legalzoom": "YOUR_LEGALZOOM_AFFILIATE_ID",
}

# Free searches allowed per browser session before the email gate kicks in.
FREE_SEARCH_LIMIT = 3

EMAILS_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "emails.csv")

REQUEST_TIMEOUT = 6  # seconds - fail fast, we always fall back gracefully

# --------------------------------------------------------------------------
# STATIC LEGAL REFERENCE TEXT (for citation panel only - not verdicts)
# --------------------------------------------------------------------------
LEGAL_REFERENCES = {
    "IN": {
        "law": "The Trade Marks Act, 1999 (India)",
        "sections": {
            "9(1)": "Absolute grounds for refusal - marks devoid of "
                     "distinctive character, marks that are purely "
                     "descriptive of the kind/quality/purpose of goods or "
                     "services, or marks that have become customary in "
                     "the trade.",
            "11": "Relative grounds for refusal - a mark cannot be "
                  "registered if it is identical/similar to an earlier "
                  "trademark for identical/similar goods or services and "
                  "there exists a likelihood of confusion, or if it is "
                  "identical/similar to a mark that already has a "
                  "reputation in India (dilution of well-known marks).",
        },
    },
    "US": {
        "law": "The Lanham Act (15 U.S.C.)",
        "sections": {
            "2(d)": "Likelihood of Confusion - the USPTO refuses "
                     "registration of a mark that so resembles a mark "
                     "already registered or used in the U.S. that it is "
                     "likely to cause confusion, mistake, or deception.",
            "2(e)(1)": "Descriptiveness - marks that merely describe an "
                        "ingredient, quality, characteristic, function, "
                        "feature, purpose, or use of the goods/services "
                        "are refused unless they have acquired "
                        "distinctiveness.",
        },
    },
}

# --------------------------------------------------------------------------
# INTERNAL REFERENCE DATASET
# A small, hand-maintained list of well-known marks used ONLY as a
# fallback signal when live lookups are unavailable. NOT a comprehensive
# trademark register - clearly labelled as such in every API response.
# --------------------------------------------------------------------------
KNOWN_FAMOUS_MARKS = [
    "google", "gemini", "chatgpt", "openai", "figma", "canva", "notion",
    "slack", "zoom", "microsoft", "windows", "apple", "iphone", "amazon",
    "netflix", "spotify", "uber", "airbnb", "meta", "facebook",
    "instagram", "whatsapp", "twitter", "tesla", "nike", "adidas",
    "sun tv", "star tv", "zee", "tata", "reliance", "infosys", "wipro",
    "byju's", "byjus", "swiggy", "zomato", "paytm", "flipkart", "ola",
    "nykaa", "dunzo", "cred", "phonepe",
]

DESCRIPTIVE_TERMS = [
    "llm", "ai", "notebook", "cloud", "smart", "app", "software", "tech",
    "digital", "data", "assistant", "bot", "chat", "market", "shop",
    "store", "health", "wellness", "fit", "food", "learn", "study",
    "astro", "astrology", "star", "stars",
]

TRADEMARK_CLASSES = {
    "9": "Class 9 - Scientific/Tech devices, downloadable software",
    "35": "Class 35 - Advertising, business management, retail",
    "41": "Class 41 - Education, entertainment, training",
    "42": "Class 42 - SaaS, technology & software design services",
    "45": "Class 45 - Personal/legal services, astrology, security",
}


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def search_uspto(brand_name: str):
    """Best-effort informational lookup. Reports 'unavailable' rather than
    silently pretending the name is clear if the live call fails."""
    result = {
        "source": "USPTO (best-effort public lookup)",
        "status": "unavailable",
        "matches": [],
        "note": None,
    }
    try:
        url = (
            "https://tmsearch.uspto.gov/api-v1-0-0/tmsearch"
            f"?query={quote_plus(brand_name)}"
        )
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "MarkScout/1.0 (informational research tool)"
        })
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
            for hit in hits[:10]:
                src = hit.get("_source", {})
                mark = src.get("mark_identification") or src.get("markLiteral")
                if mark:
                    result["matches"].append({
                        "mark": mark,
                        "status": src.get("status_type", "unknown"),
                        "similarity": round(_similarity(brand_name, mark), 2),
                    })
            result["status"] = "ok"
        else:
            result["note"] = (
                f"USPTO lookup returned HTTP {resp.status_code}; "
                "live data unavailable, falling back to reference dataset."
            )
    except Exception as exc:  # noqa: BLE001
        result["note"] = (
            "USPTO live lookup could not be completed "
            f"({type(exc).__name__}); falling back to reference dataset."
        )
    return result


def search_ip_india(brand_name: str):
    """Best-effort attempt against the public IP India portal. The portal
    is session/captcha protected, so an unauthenticated request generally
    cannot return real results - we report that honestly."""
    result = {
        "source": "IP India public search (best-effort)",
        "status": "unavailable",
        "matches": [],
        "note": None,
    }
    try:
        url = "https://tmrsearch.ipindia.gov.in/tmrpublicsearch/"
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "MarkScout/1.0 (informational research tool)"
        })
        if resp.status_code == 200:
            BeautifulSoup(resp.text, "html.parser")  # parsed but not usable
            result["note"] = (
                "IP India's public portal requires session/captcha "
                "verification that an automated script cannot complete. "
                "Live results unavailable; using reference dataset "
                "fallback below."
            )
        else:
            result["note"] = f"IP India portal returned HTTP {resp.status_code}."
    except Exception as exc:  # noqa: BLE001
        result["note"] = (
            f"IP India live lookup could not be completed "
            f"({type(exc).__name__}); using reference dataset fallback."
        )
    return result


def check_reference_dataset(brand_name: str):
    name_lower = brand_name.lower().strip()
    tokens = re.findall(r"[a-z0-9]+", name_lower)

    famous_hits = []
    for mark in KNOWN_FAMOUS_MARKS:
        sim = _similarity(name_lower, mark)
        if mark in name_lower or sim > 0.75:
            famous_hits.append({"mark": mark, "similarity": round(sim, 2)})

    descriptive_hits = [t for t in tokens if t in DESCRIPTIVE_TERMS]

    return {
        "source": "Internal reference dataset "
                   f"({len(KNOWN_FAMOUS_MARKS)} well-known marks, "
                   "not exhaustive)",
        "famous_mark_hits": famous_hits,
        "descriptive_term_hits": descriptive_hits,
    }


def compute_risk(brand_name: str, country: str, tm_class: str):
    uspto = search_uspto(brand_name) if country == "US" else None
    ipindia = search_ip_india(brand_name) if country == "IN" else None
    reference = check_reference_dataset(brand_name)

    live_matches = []
    if uspto:
        live_matches.extend(uspto["matches"])
    if ipindia:
        live_matches.extend(ipindia["matches"])

    high_similarity_live = [m for m in live_matches if m.get("similarity", 0) >= 0.85]

    level = "GREEN"
    headline = "No direct conflicts found in the sources this tool could check."
    reasons = []
    cited_sections = []

    if reference["famous_mark_hits"] or high_similarity_live:
        level = "RED"
        headline = "Possible direct collision with an existing / well-known mark."
        for hit in reference["famous_mark_hits"]:
            reasons.append(
                f"'{brand_name}' is highly similar to the well-known mark "
                f"'{hit['mark']}' (similarity {hit['similarity']})."
            )
        for hit in high_similarity_live:
            reasons.append(
                f"Live search found an existing mark '{hit['mark']}' "
                f"with similarity {hit['similarity']}."
            )
        cited_sections.append(("IN", "11") if country == "IN" else ("US", "2(d)"))

    elif reference["descriptive_term_hits"]:
        level = "YELLOW"
        headline = "Contains a generic/descriptive term that may fail distinctiveness."
        reasons.append(
            "Contains the term(s) "
            f"{', '.join(sorted(set(reference['descriptive_term_hits'])))}, "
            "which examiners commonly treat as descriptive of the goods/"
            "services rather than as a distinctive brand element."
        )
        cited_sections.append(("IN", "9(1)") if country == "IN" else ("US", "2(e)(1)"))

    data_limited = False
    if country == "US" and uspto and uspto["status"] != "ok":
        data_limited = True
    if country == "IN" and ipindia is not None:
        data_limited = True

    return {
        "level": level,
        "headline": headline,
        "reasons": reasons,
        "cited_sections": cited_sections,
        "sources_checked": {
            "uspto": uspto,
            "ip_india": ipindia,
            "reference_dataset": reference,
        },
        "data_limited": data_limited,
        "disclaimer": (
            "This is an automated, preliminary screen based on a small "
            "reference dataset and best-effort public lookups. It is NOT "
            "legal advice, NOT a comprehensive trademark clearance search, "
            "and NOT a guarantee the name is free to use or register. "
            "Before committing to a name, run a professional clearance "
            "search or consult a trademark attorney."
        ),
    }


PREFIXES = ["Nova", "Vero", "Astra", "Lumen", "Orbis", "Kairo", "Zynth",
            "Elyra", "Vantra", "Solace", "Mira", "Quanta"]
SUFFIXES = ["ify", "loop", "wave", "forge", "hub", "sphere", "craft",
            "grid", "flow", "verse", "lane", "works"]
TLDS = [".ai", ".app", ".io", ".co", ".in"]


def generate_pivots(brand_name: str, count: int = 3):
    seed_tokens = re.findall(r"[a-zA-Z]+", brand_name)
    root = (seed_tokens[0] if seed_tokens else "Brand").capitalize()

    pivots = set()
    attempts = 0
    while len(pivots) < count and attempts < 50:
        attempts += 1
        style = random.choice(["prefix", "suffix", "blend"])
        if style == "prefix":
            candidate = random.choice(PREFIXES) + root
        elif style == "suffix":
            candidate = root + random.choice(SUFFIXES).capitalize()
        else:
            candidate = random.choice(PREFIXES) + random.choice(SUFFIXES).capitalize()
        candidate += random.choice(TLDS)
        low = candidate.lower()
        if any(fm in low for fm in KNOWN_FAMOUS_MARKS):
            continue
        pivots.add(candidate)

    return list(pivots)[:count]


def generate_premium_names(brand_name: str, count: int = 50):
    """Rule-based generation for the premium upsell. Labelled honestly as
    'AI-generated suggestions to research further' - never as pre-vetted /
    guaranteed-clear, since no automated tool can guarantee that."""
    names = set()
    attempts = 0
    while len(names) < count and attempts < 500:
        attempts += 1
        style = random.choice(["prefix", "suffix", "blend", "double"])
        if style == "prefix":
            candidate = random.choice(PREFIXES) + random.choice(SUFFIXES).capitalize()
        elif style == "suffix":
            candidate = random.choice(PREFIXES) + random.choice(PREFIXES)
        elif style == "blend":
            a = random.choice(PREFIXES)
            b = random.choice(SUFFIXES)
            candidate = a[: max(3, len(a) // 2)] + b.capitalize()
        else:
            candidate = (random.choice(PREFIXES) + random.choice(SUFFIXES).capitalize()
                         + random.choice(SUFFIXES).capitalize())
        candidate += random.choice(TLDS)
        low = candidate.lower()
        if any(fm in low for fm in KNOWN_FAMOUS_MARKS):
            continue
        names.add(candidate)
    return list(names)[:count]


def build_affiliate_links(brand_name: str):
    slug = re.sub(r"[^a-z0-9]", "", brand_name.lower())
    domain_guess = f"{slug}.com"
    return {
        "namecheap": (
            "https://www.namecheap.com/domains/registration/results/"
            f"?domain={quote_plus(domain_guess)}"
            f"&affid={AFFILIATE_IDS['namecheap']}"
        ),
        "godaddy": (
            "https://www.godaddy.com/domainsearch/find"
            f"?domainToCheck={quote_plus(domain_guess)}"
            f"&isc={AFFILIATE_IDS['godaddy']}"
        ),
        "hostinger": (
            "https://www.hostinger.com/domain-checker"
            f"?domain={quote_plus(domain_guess)}"
            f"&ref={AFFILIATE_IDS['hostinger']}"
        ),
    }


def build_legal_referral_links(country: str):
    """
    Referral links to real trademark filing services / attorneys, shown
    only on RED or YELLOW verdicts - i.e. when someone actually has a
    reason to want professional help, not as a generic upsell.
    """
    if country == "IN":
        return {
            "vakilsearch": (
                "https://vakilsearch.com/trademark-registration"
                f"?ref={REFERRAL_IDS['vakilsearch']}"
            ),
            "legalraasta": (
                "https://www.legalraasta.com/trademark-registration/"
                f"?ref={REFERRAL_IDS['legalraasta']}"
            ),
            "indiafilings": (
                "https://www.indiafilings.com/trademark-registration"
                f"?ref={REFERRAL_IDS['indiafilings']}"
            ),
        }
    return {
        "trademarkia": (
            "https://www.trademarkia.com/register-trademark"
            f"?ref={REFERRAL_IDS['trademarkia']}"
        ),
        "legalzoom": (
            "https://www.legalzoom.com/business/intellectual-property/"
            f"trademark-registration.html?ref={REFERRAL_IDS['legalzoom']}"
        ),
    }


def _save_captured_email(email: str):
    """
    Appends a captured email to a local CSV. This is a simple starting
    point, not a mailing list platform. Two things to know before you
    rely on it in production:
      1. Many free hosting tiers (including Render's free tier) do NOT
         persist the filesystem across deploys/restarts - this file can
         disappear. For anything beyond quick local testing, swap this
         function to call a real ESP's API (Mailchimp, ConvertKit,
         Brevo, etc.) instead of - or in addition to - writing the CSV.
      2. This performs no double opt-in / verification. Add that before
         you send marketing email to these addresses, to stay compliant
         with anti-spam law in your country.
    """
    os.makedirs(os.path.dirname(EMAILS_CSV_PATH), exist_ok=True)
    is_new = not os.path.exists(EMAILS_CSV_PATH)
    with open(EMAILS_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["email", "captured_at_utc"])
        writer.writerow([email, datetime.now(timezone.utc).isoformat()])


@app.route("/")
def index():
    return render_template(
        "index.html",
        tm_classes=TRADEMARK_CLASSES,
        legal_refs=LEGAL_REFERENCES,
    )


@app.route("/api/check", methods=["POST"])
def api_check():
    payload = request.get_json(silent=True) or {}
    brand_name = (payload.get("brand_name") or "").strip()
    country = (payload.get("country") or "US").strip().upper()
    tm_class = (payload.get("tm_class") or "42").strip()

    if not brand_name:
        return jsonify({"error": "brand_name is required"}), 400
    if country not in ("US", "IN"):
        return jsonify({"error": "country must be 'US' or 'IN'"}), 400

    # --- Freemium gate: N free searches per browser session, then email
    # unlocks unlimited use. Session-based, so it resets in a new browser
    # / incognito window - that's an accepted tradeoff for the simplicity
    # of not requiring a database for this MVP.
    if not session.get("unlocked"):
        used = session.get("search_count", 0)
        if used >= FREE_SEARCH_LIMIT:
            return jsonify({
                "error": "limit_reached",
                "message": (
                    f"You've used your {FREE_SEARCH_LIMIT} free screens. "
                    "Drop your email to keep going - no card required."
                ),
                "free_limit": FREE_SEARCH_LIMIT,
            }), 402
        session["search_count"] = used + 1

    risk = compute_risk(brand_name, country, tm_class)

    response = {
        "brand_name": brand_name,
        "country": country,
        "tm_class": tm_class,
        "tm_class_label": TRADEMARK_CLASSES.get(tm_class, "Unknown class"),
        "risk": risk,
        "legal_context": {
            "law": LEGAL_REFERENCES[country]["law"],
            "cited_sections": [
                {"section": sec, "text": LEGAL_REFERENCES[jur]["sections"][sec]}
                for jur, sec in risk["cited_sections"]
            ],
        },
    }

    if risk["level"] in ("RED", "YELLOW"):
        response["pivots"] = generate_pivots(brand_name)
        response["legal_referral_links"] = build_legal_referral_links(country)

    if risk["level"] == "GREEN":
        response["affiliate_links"] = build_affiliate_links(brand_name)

    return jsonify(response)


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    """Captures an email to lift the free-search cap for this session."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()

    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Enter a valid email address."}), 400

    session["unlocked"] = True
    session["email"] = email

    try:
        _save_captured_email(email)
    except Exception:  # noqa: BLE001
        # Don't block the unlock if the disk write fails (e.g. read-only
        # filesystem on some hosts) - the person still gets access; you'll
        # just want to check your hosting provider's filesystem behavior
        # and/or wire up a real ESP per the note on _save_captured_email.
        pass

    return jsonify({"ok": True})


@app.route("/api/premium-names", methods=["POST"])
def api_premium_names():
    """In production, gate this behind a real payment confirmation (verify
    a Stripe/PayPal webhook or session before calling this). The bundled
    frontend treats the checkout button as illustrative and does not
    silently charge anyone - see README.txt for wiring up real payments."""
    payload = request.get_json(silent=True) or {}
    brand_name = (payload.get("brand_name") or "brand").strip()
    names = generate_premium_names(brand_name, count=50)
    return jsonify({
        "brand_name": brand_name,
        "names": names,
        "note": (
            "These are rule/AI-generated name suggestions to research "
            "further - they are not pre-cleared or guaranteed available. "
            "Run each candidate through the risk checker before use."
        ),
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
