"""Email sending. Real SMTP when SMTP_HOST is set; otherwise nothing is sent (OTP login is disabled
unless ALLOW_DEV_OTP=1, which writes mails to outbox.log and shows the code on screen: development only)."""
import html, json, os, smtplib, ssl, threading, time, urllib.error, urllib.request
from email.message import EmailMessage

if os.path.exists(".env"):  # KEY=value lines; keeps secrets out of the shell and chat
    for _l in open(".env", encoding="utf-8-sig"):  # utf-8-sig: tolerate a BOM added by some editors
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

HOST, USER, PASS = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
PORT = int(os.getenv("SMTP_PORT", "587"))
SECURITY = os.getenv("SMTP_SECURITY", "ssl" if PORT == 465 else "starttls").lower()  # starttls | ssl | none
FROM = os.getenv("SMTP_FROM", USER or "support@technest.local")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://127.0.0.1:8000")
BREVO_KEY = os.getenv("BREVO_API_KEY")  # HTTPS email API: works on hosts that block SMTP ports (e.g. Render free)
BREVO_URL = os.getenv("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
BREVO_SENDER = os.getenv("BREVO_SENDER") or FROM
CONFIGURED = bool(HOST or BREVO_KEY)
PROVIDER = "brevo-api" if BREVO_KEY else ("smtp" if HOST else None)
ALLOW_DEV_OTP = os.getenv("ALLOW_DEV_OTP") == "1" and not CONFIGURED
DEV = not CONFIGURED


class Refused(Exception):
    """The mail server rejected the recipient address."""


def _brevo(to, subject, text, body_html):
    req = urllib.request.Request(
        BREVO_URL, method="POST",
        data=json.dumps({"sender": {"name": "TechNest Support", "email": BREVO_SENDER}, "to": [{"email": to}],
                         "subject": subject, "htmlContent": body_html, "textContent": text}).encode(),
        headers={"api-key": BREVO_KEY, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf8", "replace")[:300]
        if e.code == 400 and "email" in detail.lower():  # malformed / rejected recipient
            raise Refused(detail)
        raise RuntimeError(f"Brevo HTTP {e.code}: {detail}")


def _log(line):
    with open("outbox.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def deliver(to, subject, text, body_html):
    """Send synchronously. Raises Refused if the recipient is rejected, other exceptions on server trouble."""
    if not CONFIGURED:
        _log(f"NOT SENT (SMTP not configured) TO={to} SUBJECT={subject}\n{text}\n{'-' * 60}")
        return
    if PROVIDER == "brevo-api":
        try:
            _brevo(to, subject, text, body_html)
            _log(f"SENT(brevo) TO={to} SUBJECT={subject}")
            return
        except Refused as e:
            _log(f"REFUSED(brevo) TO={to} SUBJECT={subject} ERROR={e}")
            raise
        except Exception as e:
            _log(f"FAILED(brevo) TO={to} SUBJECT={subject} ERROR={e}")
            raise
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"TechNest Support <{FROM}>", to, subject
    m.set_content(text)
    m.add_alternative(body_html, subtype="html")
    try:
        cls = smtplib.SMTP_SSL if SECURITY == "ssl" else smtplib.SMTP
        kw = {"context": ssl.create_default_context()} if SECURITY == "ssl" else {}
        with cls(HOST, PORT, timeout=20, **kw) as s:
            if SECURITY == "starttls":
                s.starttls(context=ssl.create_default_context())
            if USER:
                s.login(USER, PASS or "")
            s.send_message(m)
        _log(f"SENT TO={to} SUBJECT={subject}")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
        _log(f"REFUSED TO={to} SUBJECT={subject} ERROR={e}")
        raise Refused(str(e))
    except Exception as e:
        _log(f"FAILED TO={to} SUBJECT={subject} ERROR={e}")
        raise


def send(to, subject, text, body_html):
    """Fire-and-forget (notifications) so API calls never wait on SMTP."""
    def run():
        try:
            deliver(to, subject, text, body_html)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def page(title, lines, footer="Reply in the TechNest support panel to continue this conversation."):
    items = "".join(f'<p style="margin:0 0 12px;line-height:1.55">{l}</p>' for l in lines)
    return (f'<div style="font-family:Segoe UI,Arial,sans-serif;background:#f4f5fb;padding:24px">'
            f'<div style="max-width:520px;margin:auto;background:#fff;border-radius:14px;padding:26px;color:#161a2b">'
            f'<div style="font-weight:700;color:#4f46e5;margin-bottom:14px">&#10022; TechNest Support</div>'
            f'<h2 style="margin:0 0 14px;font-size:20px">{html.escape(title)}</h2>{items}'
            f'<p style="color:#667085;font-size:13px;margin-top:20px">{html.escape(footer)}<br>{PUBLIC_URL}</p></div></div>')
