"""Email sending. Real SMTP when SMTP_HOST is set; otherwise nothing is sent (OTP login is disabled
unless ALLOW_DEV_OTP=1, which writes mails to outbox.log and shows the code on screen: development only)."""
import html, os, smtplib, ssl, threading, time
from email.message import EmailMessage

if os.path.exists(".env"):  # KEY=value lines; keeps secrets out of the shell and chat
    for _l in open(".env", encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

HOST, USER, PASS = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
PORT = int(os.getenv("SMTP_PORT", "587"))
SECURITY = os.getenv("SMTP_SECURITY", "ssl" if PORT == 465 else "starttls").lower()  # starttls | ssl | none
FROM = os.getenv("SMTP_FROM", USER or "support@technest.local")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://127.0.0.1:8000")
CONFIGURED = bool(HOST)
ALLOW_DEV_OTP = os.getenv("ALLOW_DEV_OTP") == "1" and not CONFIGURED
DEV = not CONFIGURED


class Refused(Exception):
    """The mail server rejected the recipient address."""


def _log(line):
    with open("outbox.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def deliver(to, subject, text, body_html):
    """Send synchronously. Raises Refused if the recipient is rejected, other exceptions on server trouble."""
    if not CONFIGURED:
        _log(f"NOT SENT (SMTP not configured) TO={to} SUBJECT={subject}\n{text}\n{'-' * 60}")
        return
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
