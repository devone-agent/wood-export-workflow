#!/usr/bin/env python3
"""
Titan email test (smtp.titan.email:465 SSL + imap.titan.email:993)
Usage: python3 test_email.py
"""
import imaplib, os, smtplib, ssl, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

USER     = os.getenv("EMAIL_USER", "pav@instructset.com")
PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP     = os.getenv("EMAIL_HOST", "smtp.titan.email")
PORT     = int(os.getenv("EMAIL_PORT", "465"))
IMAP     = os.getenv("IMAP_HOST", "imap.titan.email")

print(f"\n{'='*50}")
print(f"  Titan Email Test")
print(f"  Account : {USER}")
print(f"  Password: {'*'*len(PASSWORD)} ({len(PASSWORD)} chars)")
print(f"  SMTP    : {SMTP}:{PORT} SSL")
print(f"  IMAP    : {IMAP}:993 SSL")
print(f"{'='*50}\n")

# ── SMTP ───────────────────────────────────────────────────────────────────────
print("1. SMTP send...")
try:
    msg = MIMEMultipart()
    msg["From"] = f"Wood Export Bot <{USER}>"
    msg["To"] = USER
    msg["Subject"] = "Wood Export Bot — Titan SMTP Test ✓"
    msg.attach(MIMEText("Titan SMTP working.\n\n— Wood Export Bot", "plain"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP, PORT, context=ctx, timeout=15) as s:
        s.login(USER, PASSWORD)
        s.sendmail(USER, [USER], msg.as_bytes())
    print(f"   ✅ SMTP OK — test email sent to {USER}")
except Exception as e:
    print(f"   ❌ SMTP FAILED: {e}")
    if "authentication" in str(e).lower():
        print("   → Enable third-party access first:")
        print("     mail.titan.email → Settings → Security → Third-party app access → Enable")

# ── IMAP ───────────────────────────────────────────────────────────────────────
print("\n2. IMAP poll...")
try:
    with imaplib.IMAP4_SSL(IMAP, 993) as imap:
        imap.login(USER, PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, "ALL")
        total = len(data[0].split()) if data[0] else 0
        _, udata = imap.search(None, "UNSEEN")
        unseen = len(udata[0].split()) if udata[0] else 0
    print(f"   ✅ IMAP OK — {total} messages, {unseen} unseen")
except Exception as e:
    print(f"   ❌ IMAP FAILED: {e}")

print(f"\n{'='*50}\n")
