import asyncio, hashlib, hmac, html, json, os, re, secrets, threading, time
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import mailer, agent, emailcheck  # mailer first: it loads .env

DB = "cases.json"
STAFF_PIN = os.getenv("STAFF_PIN", "")  # empty = dashboard is open (set a PIN in .env to protect it)
OTP_TTL, SESSION_TTL, RESEND_GAP, MAX_TRIES, MAX_PER_HOUR = 600, 86400, 30, 5, 5
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
lock = threading.Lock()
cases = json.load(open(DB)) if os.path.exists(DB) else {}
otps, sessions = {}, {}  # email -> otp state, token -> {email, exp}
app = FastAPI(title="TechNest Autonomous Support Agent")


def save():
    json.dump(cases, open(DB, "w"), indent=1)


# ---------- auth ----------
def hsh(email, code):
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def who(token, staff):
    """-> (customer email or None, is_staff)"""
    s = sessions.get(token or "")
    email = s["email"] if s and s["exp"] > time.time() else None
    is_staff = (not STAFF_PIN) or (bool(staff) and hmac.compare_digest(staff, STAFF_PIN))
    return email, is_staff


def need_customer(token):
    email, _ = who(token, None)
    if not email:
        raise HTTPException(401, "Please verify your email to continue.")
    return email


@app.get("/api/config")
def config():
    return {"staff_pin_required": bool(STAFF_PIN)}


def need_staff(staff):
    if STAFF_PIN and not (staff and hmac.compare_digest(staff, STAFF_PIN)):
        raise HTTPException(401, "Invalid staff PIN.")


def get(i):
    if i not in cases:
        raise HTTPException(404, "case not found")
    return cases[i]


def own(i, token):
    c, email = get(i), need_customer(token)
    if c.get("email") != email:
        raise HTTPException(403, "This case belongs to a different email address.")
    return c


class EmailIn(BaseModel):
    email: str


class Verify(BaseModel):
    email: str
    code: str


@app.get("/api/email/check")
def email_check(email: str = ""):
    ok, msg = emailcheck.check(email)
    return {"ok": ok, "msg": msg}


@app.post("/api/otp/send")
def otp_send(b: EmailIn):
    email = b.email.strip().lower()
    ok, msg = emailcheck.check(email)
    if not ok:
        raise HTTPException(422, msg)
    if not mailer.CONFIGURED and not mailer.ALLOW_DEV_OTP:
        raise HTTPException(503, "Email service is not set up yet, so we can't send verification codes. "
                                 "Ask the administrator to configure SMTP (see .env.example).")
    now = time.time()
    st = otps.setdefault(email, {"sent": []})
    st["sent"] = [t for t in st["sent"] if now - t < 3600]
    if st["sent"] and now - st["sent"][-1] < RESEND_GAP:
        raise HTTPException(429, f"Please wait {int(RESEND_GAP - (now - st['sent'][-1])) + 1}s before requesting another code.")
    if len(st["sent"]) >= MAX_PER_HOUR:
        raise HTTPException(429, "Too many codes requested. Try again in an hour.")
    code = f"{secrets.randbelow(10**6):06d}"
    try:  # synchronous: if the mail server rejects the address, tell the user right now
        mailer.deliver(email, f"{code} is your TechNest verification code",
                       f"Your TechNest verification code is {code}. It is valid for 10 minutes.\nIf you did not request it, ignore this email.",
                       mailer.page("Verify your email", [f'Your verification code is <b style="font-size:26px;letter-spacing:4px">{code}</b>',
                                                         "It is valid for 10 minutes. If you did not request it, you can ignore this email."]))
    except mailer.Refused:
        raise HTTPException(422, "That email address doesn't seem to exist. Please check it and try again.")
    except Exception:
        raise HTTPException(502, "We couldn't send the email right now. Please try again shortly.")
    st.update(h=hsh(email, code), exp=now + OTP_TTL, tries=0)
    st["sent"].append(now)
    return {"ok": True, "dev_code": code if mailer.ALLOW_DEV_OTP else None}


@app.post("/api/otp/verify")
def otp_verify(b: Verify):
    email = b.email.strip().lower()
    st = otps.get(email)
    if not st or "h" not in st or st["exp"] < time.time():
        raise HTTPException(400, "Code expired. Please request a new one.")
    st["tries"] += 1
    if st["tries"] > MAX_TRIES:
        st.pop("h", None)
        raise HTTPException(429, "Too many wrong attempts. Please request a new code.")
    if not hmac.compare_digest(st["h"], hsh(email, b.code.strip())):
        raise HTTPException(400, f"Incorrect code. {MAX_TRIES - st['tries']} attempt(s) left.")
    st.pop("h", None)
    token = secrets.token_urlsafe(32)
    sessions[token] = {"email": email, "exp": time.time() + SESSION_TTL}
    return {"token": token, "email": email}


# ---------- email notifications ----------
def notify(c, before):
    """Email the customer about every new agent-side event produced by one operation."""
    lines, subject = [], None
    for e in c["timeline"][before:]:
        t = html.escape(e["text"])
        if e["kind"] == "offer":
            lines.append(f"<b>{html.escape(e['actor'])}:</b> {t}")
            subject = f"New offer on your case #{c['id']} from {e['actor']}"
        elif e["kind"] == "escalate":
            lines.append(f"&#8593; {t}")
            subject = subject or f"Your case #{c['id']} was escalated"
        elif e["kind"] == "system" and (e["text"].startswith("Case CLOSED") or e["text"].startswith("All automated")):
            lines.append(t)
            subject = f"Your case #{c['id']} is resolved" if c["status"] == "resolved" else f"Regional Manager will call you - case #{c['id']}"
    if before == 0:
        subject = f"We received your complaint - case #{c['id']}"
        lines.insert(0, f"Hi {html.escape(c['customer'])}, we received your complaint about your <b>{html.escape(c['product'])}</b>.")
    if not lines:
        return
    text = re.sub(r"<[^>]+>", "", "\n\n".join(lines)).replace("&#8593;", "^")
    mailer.send(c["email"], subject, text + f"\n\nOpen the panel: {mailer.PUBLIC_URL}",
                mailer.page(subject, lines))


# ---------- cases ----------
class Form(BaseModel):
    customer: str
    email: str
    product: str
    amount: float
    days: int
    in_warranty: bool
    complaint: str
    wanted: str = "auto"


class Reply(BaseModel):
    accept: bool
    comment: str = ""


class Msg(BaseModel):
    text: str


class Rate(BaseModel):
    rating: int


@app.post("/api/cases")
def new(f: Form, x_token: str = Header(None)):
    email = need_customer(x_token)
    if f.email.strip().lower() != email:
        raise HTTPException(403, "The email must match your verified address.")
    with lock:
        hist = [h for h in cases.values() if h.get("email") == email]  # identity = verified email
        c = agent.create_case({**f.model_dump(), "email": email}, hist)
        cases[c["id"]] = c
        save()
        notify(c, 0)
        return c


@app.get("/api/my-cases")
def my_cases(x_token: str = Header(None)):
    email = need_customer(x_token)
    return sorted([c for c in cases.values() if c.get("email") == email], key=lambda c: -c["created"])


@app.get("/api/cases")
def all_cases(x_staff: str = Header(None)):
    need_staff(x_staff)
    return sorted(cases.values(), key=lambda c: -c["created"])


@app.get("/api/cases/{i}")
def one(i: str, x_token: str = Header(None), x_staff: str = Header(None)):
    email, is_staff = who(x_token, x_staff)
    c = get(i)
    if is_staff or (email and c.get("email") == email):
        return c
    raise HTTPException(401 if not email else 403, "You are not allowed to view this case.")


def mutate(i, token, fn):
    c = own(i, token)
    with lock:
        before = len(c["timeline"])
        fn(c)
        save()
        notify(c, before)
        return c


@app.post("/api/cases/{i}/respond")
def respond(i: str, r: Reply, x_token: str = Header(None)):
    return mutate(i, x_token, lambda c: agent.respond(c, r.accept, r.comment))


@app.post("/api/cases/{i}/message")
def msg(i: str, m: Msg, x_token: str = Header(None)):
    return mutate(i, x_token, lambda c: agent.message(c, m.text))


@app.post("/api/cases/{i}/escalate")
def esc(i: str, x_token: str = Header(None)):
    return mutate(i, x_token, lambda c: agent.force_escalate(c, "Customer requested a senior."))


@app.post("/api/cases/{i}/rate")
def rate(i: str, r: Rate, x_token: str = Header(None)):
    c = own(i, x_token)
    with lock:
        if c["status"] == "resolved":
            c["rating"] = max(1, min(5, r.rating))
            save()
        return c


@app.get("/api/stats")
def stats(x_staff: str = Header(None)):
    need_staff(x_staff)
    cs = list(cases.values())
    res = [c for c in cs if c["status"] == "resolved"]
    rated = [c["rating"] for c in cs if c.get("rating")]
    esc_n = [sum(1 for e in c["timeline"] if e["kind"] == "escalate") for c in cs]
    return {"total": len(cs), "resolved": len(res),
            "open": sum(1 for c in cs if c["status"] == "awaiting_customer"),
            "manual_review": sum(1 for c in cs if c["status"] == "manual_review"),
            "by_level": {l: sum(1 for c in res if c.get("resolved_level") == l) for l in (1, 2, 3)},
            "avg_escalations": round(sum(esc_n) / len(esc_n), 2) if cs else 0,
            "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
            "avg_resolve_sec": round(sum(c["resolved_at"] - c["created"] for c in res if c.get("resolved_at")) / max(1, len(res)))}


async def sla_loop():
    while True:
        await asyncio.sleep(3)
        with lock:
            changed = False
            for c in cases.values():
                before = len(c["timeline"])
                if agent.check_sla(c):
                    changed = True
                    notify(c, before)
            if changed:
                save()
        for k in [k for k, s in sessions.items() if s["exp"] < time.time()]:
            sessions.pop(k, None)


@app.on_event("startup")
async def _start():
    asyncio.create_task(sla_loop())
    print("[mail] " + (f"SMTP ready ({mailer.HOST}:{mailer.PORT}): real emails enabled" if mailer.CONFIGURED else
          "DEV OTP (ALLOW_DEV_OTP=1): codes shown on screen, NOT emailed" if mailer.ALLOW_DEV_OTP else
          "SMTP NOT configured: verification codes cannot be sent. See .env.example"))
    print("[staff] dashboard " + ("protected by STAFF_PIN" if STAFF_PIN else "is OPEN (no PIN)"))


@app.get("/")
def index():
    return FileResponse("static/index.html")
