"""TechNest autonomous escalation agent: analyse -> route -> offer within power -> escalate until resolved."""
import json, os, re, time

STAFF = {1: ("Rahul Sharma", "Customer Service Executive"),
         2: ("Priya Nair", "Store Manager"),
         3: ("Vikram Mehta", "Regional Manager")}
# SLA per level in seconds (2h / 4h / 24h). DEMO_SLA_SECONDS overrides for quick demos.
SLA = {1: 7200, 2: 14400, 3: 86400}
if os.getenv("DEMO_SLA_SECONDS"):
    SLA = {k: int(os.environ["DEMO_SLA_SECONDS"]) for k in SLA}
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
COMP_STEPS = [5000, 10000, 25000, 50000]
_client = None
try:
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        _client = anthropic.Anthropic()
except Exception:
    _client = None


def llm(prompt, max_tokens=300):
    if not _client:
        return None
    try:
        r = _client.messages.create(model=MODEL, max_tokens=max_tokens,
                                    messages=[{"role": "user", "content": prompt}])
        return r.content[0].text.strip()
    except Exception:
        return None


# ---------- 1. Understand ----------
def analyse(text, wanted):
    """Classify complaint -> dict. Keyword heuristic first, LLM refines when a key is configured."""
    t = text.lower()
    a = {"requested": wanted if wanted != "auto" else "none",
         "defect": bool(re.search(r"defect|flicker|not work|broken|dead|crack|faulty|stopped|issue|problem", t)),
         "safety": bool(re.search(r"overheat|fire|smoke|burn|explod|swell|shock|spark", t)),
         "legal": bool(re.search(r"lawyer|legal|court|consumer forum|sue|police", t)),
         "staff_complaint": bool(re.search(r"rude|misbehav|insult|shouted|abus|unprofessional", t)),
         "wants_senior": bool(re.search(r"manager|senior|supervisor|higher", t)),
         "angry": bool(re.search(r"angry|furious|worst|unacceptable|fed up|cheat|fraud|scam", t))}
    if a["requested"] == "none":
        for k, rx in (("refund", r"refund|money back"), ("replace", r"replac|exchange"),
                      ("repair", r"repair|fix"), ("compensation", r"compensat"),
                      ("info", r"status|warranty|policy|how |when |where ")):
            if re.search(rx, t):
                a["requested"] = k
                break
    out = llm('Analyse this retail complaint. Reply ONLY JSON with keys defect,safety,legal,staff_complaint,'
              'wants_senior,angry (booleans) and requested (refund|replace|repair|compensation|info|none). '
              f'Customer wants: {wanted}. Complaint: """{text}"""')
    if out:
        try:
            a.update({k: v for k, v in json.loads(out[out.index("{"):out.rindex("}") + 1]).items() if k in a})
            if wanted != "auto":
                a["requested"] = wanted
        except Exception:
            pass
    return a


def start_level(a):
    if a["safety"] or a["legal"]:
        return 3
    if a["staff_complaint"] or a["wants_senior"]:
        return 2
    return 1


# ---------- 2. Policy engine (hard power limits, never delegated to the LLM) ----------
def allowed(level, act, c, amt=None):
    f, d, w = c["amount"], c["days"], c["in_warranty"]
    vip = c.get("vip")
    d = max(0, d - 3) if vip else d  # VIP customers get +3 days on every window
    defect = c["analysis"]["defect"]
    return {
        "refund": (level == 1 and f <= (5000 if vip else 2000) and d <= 7 and defect) or (level == 2 and f <= 15000 and d <= 30) or level == 3,
        "replace": (level == 1 and defect and d <= 7) or (level == 2 and d <= 30) or level == 3,
        "repair": (level == 1 and w) or level >= 2,
        "loaner": level >= 2,
        "discount": True,
        "gift": level >= 2,
        "compensation": level == 3 and (amt or 0) <= 50000,
        "investigate": level == 3,
        "info": True,
    }.get(act, False)


PCT = {1: 5, 2: 15, 3: 25}
ORDER = {"refund": ["refund", "replace", "repair", "discount"],
         "replace": ["replace", "refund", "repair", "discount"],
         "repair": ["repair", "replace", "refund", "discount"],
         "compensation": ["compensation", "refund", "replace", "discount"],
         "info": ["info"], "none": ["repair", "replace", "discount"]}


def candidates(level, c):
    """Ordered offers this level may legally make, best match to the customer's request first."""
    a, out = c["analysis"], []
    if a["staff_complaint"]:
        order = ["apology", "discount"] + (["investigate"] if level == 3 else [])
    elif a["safety"]:
        order = ["replace", "refund", "investigate"]
    else:
        order = ORDER.get(a["requested"], ORDER["none"])
    if level == 3 and a["requested"] != "info":
        order = order + ["compensation"]
    for act in order:
        if act == "apology":
            out.append({"action": "apology", "text": "a formal apology and a 5% goodwill coupon"})
        elif act == "discount":
            out.append({"action": "discount", "text": f"a {PCT[level]}% discount coupon on your next purchase"})
        elif act == "compensation":
            for s in COMP_STEPS:
                if allowed(level, "compensation", c, s):
                    out.append({"action": "compensation", "text": f"Rs {s:,} compensation for the inconvenience"})
        elif act == "info":
            out.append({"action": "info", "text": "the information you asked about: your warranty is "
                        + ("active" if c["in_warranty"] else "expired") + f" and the purchase was {c['days']} day(s) ago"})
        elif allowed(level, act, c):
            if act == "refund":
                x = {"action": "refund", "text": f"a full refund of Rs {c['amount']:,.0f}"}
                if level == 3:
                    x["text"] += " plus a 10% goodwill voucher"
            elif act == "replace":
                x = {"action": "replace", "text": "a brand-new replacement unit"
                     + (" with a loaner device until it arrives" if level >= 2 else "")}
            elif act == "repair":
                x = {"action": "repair", "text": "a free repair"
                     + (" with express turnaround and a loaner device" if level >= 2 else "")}
            else:
                x = {"action": "investigate", "text": "an internal investigation, with findings shared with you"}
            out.append(x)
    seen, res = set(), []
    for o in out:  # de-duplicate, drop already-rejected offers
        if o["text"] not in seen and o["text"] not in c["rejected"]:
            seen.add(o["text"])
            res.append(o)
    return res


# ---------- 3. Agent loop ----------
def log(c, kind, text, level=None, why=None):
    lv = level or c["level"]
    c["timeline"].append({"t": time.time(), "level": lv, "kind": kind, "text": text, "why": why,
                          "actor": "System" if kind == "system" else ("Customer" if kind == "customer" else STAFF[lv][0])})


def say(c, offer):
    name, post = STAFF[c["level"]]
    base = f"Hello {c['customer']}, this is {name}, {post}. I can offer you {offer['text']}. Would this resolve your issue?"
    msg = llm(f"You are {name}, {post} at TechNest Electronics. Write a warm, concise (max 45 words) message to "
              f"{c['customer']} whose complaint was: \"{c['complaint']}\". Offer: {offer['text']}. "
              "End by asking if this resolves the issue. No markdown.")
    return msg or base


def why(c, o):
    req, lv = c["analysis"]["requested"], c["level"]
    if c["analysis"]["safety"]:
        why_ = "Safety risk detected: replacing/refunding first and flagging for investigation."
    elif o["action"] == req:
        why_ = f"Matches the customer's request ({req}) and is within L{lv} authority."
    elif req in ("none", "info"):
        why_ = "Best fit for the issue described."
    else:
        why_ = f"Requested '{req}' is beyond L{lv} authority for this case, so offering the closest allowed option."
    if c.get("vip"):
        why_ += " VIP loyalty perks applied."
    return why_


def priority(c):
    a = c["analysis"]
    n = 3 * (a["safety"] or a["legal"]) + 2 * a["angry"] + a["staff_complaint"] + (c["amount"] > 20000) + bool(c.get("vip"))
    return "High" if n >= 3 else "Medium" if n >= 1 else "Low"


def step(c):
    """Next move on an open case: offer something, or escalate if this level has nothing left to give."""
    while c["status"] == "open":
        lv = c["level"]
        cand = candidates(lv, c)
        if not cand:
            if lv < 3:
                log(c, "escalate", f"{STAFF[lv][0]} has nothing left within their authority. Escalating to "
                    f"{STAFF[lv+1][0]} ({STAFF[lv+1][1]}) with the full case history.")
                c["level"] += 1
                c["level_since"] = time.time()
                continue
            c["status"] = "manual_review"
            log(c, "system", "All automated options are exhausted. The Regional Manager will personally call the customer.")
            return c
        c["current_offer"] = cand[0]
        c["status"] = "awaiting_customer"
        c["level_since"] = time.time()
        log(c, "offer", say(c, cand[0]), why=why(c, cand[0]))
    return c


def create_case(form, history=()):
    c = {"id": str(int(time.time() * 1000))[-8:], "customer": form["customer"], "email": form["email"], "product": form["product"],
         "amount": float(form["amount"]), "days": int(form["days"]), "in_warranty": bool(form["in_warranty"]),
         "complaint": form["complaint"], "created": time.time(), "status": "open", "rejected": [],
         "timeline": [], "current_offer": None, "resolution": None}
    c["analysis"] = analyse(c["complaint"], form.get("wanted", "auto"))
    done = [h for h in history if h["status"] == "resolved"]
    refunds = sum(1 for h in done if h["resolution"]["action"] in ("refund", "compensation"))
    c["abuse_risk"] = refunds >= 3
    c["vip"] = len(done) >= 3 and not c["abuse_risk"]
    c["level"] = max(start_level(c["analysis"]), 2 if c["abuse_risk"] else 1)
    c["level_since"] = time.time()
    c["priority"] = priority(c)
    a = c["analysis"]
    log(c, "system", f"Complaint received. Agent analysis: wants={a['requested']}, defect={a['defect']}, "
        f"safety={a['safety']}, legal={a['legal']}, staff_complaint={a['staff_complaint']}. "
        f"Priority={c['priority']}. Routed to L{c['level']} {STAFF[c['level']][0]}.")
    if c["vip"]:
        log(c, "system", "Returning loyal customer: VIP perks applied.")
    if c["abuse_risk"]:
        log(c, "system", "Frequent refund history detected: skipping L1, routed to manager review.")
    return step(c)


def respond(c, accept, comment=""):
    if c["status"] != "awaiting_customer":
        return c
    o = c["current_offer"]
    if accept:
        c["status"] = "resolved"
        c["resolution"] = o
        c["resolved_level"], c["resolved_at"] = c["level"], time.time()
        log(c, "customer", "Customer accepted the offer. " + comment)
        log(c, "system", f"Case CLOSED. Resolution: {o['text']}.")
        return c
    c["rejected"].append(o["text"])
    c["status"] = "open"
    log(c, "customer", "Customer rejected the offer. " + comment)
    if c["level"] < 3:  # rejection -> next senior
        c["level"] += 1
        log(c, "escalate", f"Escalating to {STAFF[c['level']][0]} ({STAFF[c['level']][1]}) with full case history.")
    else:
        log(c, "system", "Regional Manager is re-negotiating with a new offer.")
    return step(c)


def force_escalate(c, reason):
    if c["status"] == "awaiting_customer" and c["level"] < 3:
        c["status"] = "open"
        c["level"] += 1
        log(c, "escalate", f"{reason} Escalating to {STAFF[c['level']][0]} ({STAFF[c['level']][1]}).")
        return step(c)
    return c


def check_sla(c):
    if c["status"] == "awaiting_customer" and c["level"] < 3 and time.time() - c["level_since"] > SLA[c["level"]]:
        force_escalate(c, "SLA time limit reached without resolution.")
        return True
    return False


# ---------- 4. Negotiation: customer free-text during an open offer ----------
ACTS = (("refund", r"refund|money back|paisa"), ("replace", r"replac|exchange|new one"),
        ("repair", r"repair|fix"), ("compensation", r"compensat|damages"))


def message(c, text):
    if c["status"] != "awaiting_customer":
        return c
    t = text.lower()
    log(c, "customer", text)
    act = next((k for k, rx in ACTS if re.search(rx, t)), None)
    angry = bool(re.search(r"angry|furious|worst|unacceptable|fed up|cheat|fraud|scam|useless|waste", t))
    if re.search(r"\b(ok|okay|accept|fine|agree|yes|sure|deal)\b", t) and not re.search(r"\b(no|not|but|don't|dont)\b", t):
        return respond(c, True, "(via message)")
    if angry:
        c["analysis"]["angry"] = True
        c["priority"] = "High"
    cur = c["current_offer"]["action"]
    if act and act != cur:  # customer counter-proposes a different remedy
        c["analysis"]["requested"] = act
        c["rejected"].append(c["current_offer"]["text"])
        cand = candidates(c["level"], c)
        if cand and cand[0]["action"] == act:  # same level can grant it -> negotiate, no escalation
            c["status"] = "open"
            log(c, "system", f"Counter-request understood ({act}). {STAFF[c['level']][0]} can grant this within authority.")
            return step(c)
        c["rejected"].pop()
        return respond(c, False, f"(asked for {act}, beyond current authority)")
    if angry or act:
        return respond(c, False, "(customer unhappy)")
    log(c, "offer", "I understand. Could you tell me what outcome you'd prefer: refund, replacement, repair or compensation? Or Accept/Reject above.")
    return c
