"""Email address screening: syntax, typo, disposable domains, and a DNS MX lookup.
A mailbox itself cannot be verified from outside; the emailed OTP is the real proof of ownership."""
import re, time
import dns.exception, dns.resolver

RX = re.compile(r"^[a-z0-9][a-z0-9._%+'\-]*@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}$")
TYPOS = {"gmial.com": "gmail.com", "gmai.com": "gmail.com", "gamil.com": "gmail.com", "gmail.co": "gmail.com",
         "gmail.con": "gmail.com", "gmal.com": "gmail.com", "gmail.in": "gmail.com", "gmil.com": "gmail.com",
         "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "yahoo.con": "yahoo.com", "hotmial.com": "hotmail.com",
         "hotmal.com": "hotmail.com", "outlok.com": "outlook.com", "outllok.com": "outlook.com", "gmail.cmo": "gmail.com"}
DISPOSABLE = {"mailinator.com", "tempmail.com", "temp-mail.org", "10minutemail.com", "guerrillamail.com", "yopmail.com",
              "trashmail.com", "throwaway.email", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc",
              "fakeinbox.com", "mintemail.com", "tempr.email", "mohmal.com"}
_cache = {}


def _lookup(domain):
    r = dns.resolver.Resolver()
    r.lifetime = r.timeout = 5
    try:
        mx = r.resolve(domain, "MX")
        hosts = [str(x.exchange).rstrip(".") for x in mx]
        if all(h == "" for h in hosts):  # "null MX": domain explicitly accepts no mail
            return (False, f"{domain} does not accept email."), True
        return (True, ""), True
    except dns.resolver.NoAnswer:  # RFC 5321: fall back to the domain's A/AAAA record
        for t in ("A", "AAAA"):
            try:
                r.resolve(domain, t)
                return (True, ""), True
            except Exception:
                pass
        return (False, f"{domain} cannot receive email."), True
    except dns.resolver.NXDOMAIN:
        return (False, f"The domain '{domain}' does not exist. Please check your email address."), True
    except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.resolver.LifetimeTimeout, OSError):
        return (False, "We couldn't verify that email domain right now. Please try again in a moment."), False
    except Exception:
        return (False, "We couldn't verify that email domain right now. Please try again in a moment."), False


def check(email):
    """-> (ok, message). Result for real domains is cached for an hour."""
    email = (email or "").strip().lower()
    if len(email) > 254 or not RX.match(email):
        return False, "Please enter a valid email address."
    local, domain = email.rsplit("@", 1)
    if domain in TYPOS:
        return False, f"Did you mean {local}@{TYPOS[domain]}?"
    if domain in DISPOSABLE:
        return False, "Temporary or disposable email addresses are not allowed."
    hit = _cache.get(domain)
    if hit and hit[1] > time.time():
        return hit[0]
    res, cacheable = _lookup(domain)
    if not cacheable:  # transient DNS hiccup: retry once before giving up
        res, cacheable = _lookup(domain)
    if cacheable:
        _cache[domain] = (res, time.time() + 3600)
    return res
