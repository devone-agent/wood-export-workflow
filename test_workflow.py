#!/usr/bin/env python3
"""
End-to-end workflow test — simulates a full buyer → supplier → quote → negotiate cycle.
Requires the server to be running: uvicorn api.main:app --reload

Usage: python3 test_workflow.py
"""
import json
import sys
import httpx

BASE = "http://localhost:8000"

def pp(label, data):
    print(f"\n{'─'*50}")
    print(f"  {label}")
    print(f"{'─'*50}")
    print(json.dumps(data, indent=2))

def check(resp, label):
    if resp.status_code not in (200, 201):
        print(f"\n❌ {label} failed [{resp.status_code}]: {resp.text}")
        sys.exit(1)
    data = resp.json()
    pp(f"✅ {label}", data)
    return data

print("\n" + "="*50)
print("  Wood Export Bot — Full Workflow Test")
print("="*50)

# ── Step 1: Submit buyer RFQ ───────────────────────────────────────────────────
print("\n► Step 1: Submit buyer demand form")
rfq_payload = {
    "buyer_name": "Raj Sharma",
    "buyer_email": "raj@indiawood.in",
    "buyer_whatsapp": "+919876543210",
    "destination_country": "India",
    "destination_port": "Nhava Sheva",
    "origin_port": "Tanjung Priok",
    "preferred_currency": "USD",
    "line_items": [
        {
            "product_type": "sawn_timber",
            "wood_species": "teak_a",
            "quality_grade": "A",
            "length": 2.4,
            "width": 0.1,
            "height": 0.05,
            "unit": "m",
            "quantity": 100,
            "quantity_unit": "pieces",
            "container_size": "20ft",
            "expected_rate": 800,
            "expected_rate_currency": "USD"
        },
        {
            "product_type": "panelling",
            "wood_species": "meranti",
            "quality_grade": "B",
            "length": 2.44,
            "width": 1.22,
            "height": 0.018,
            "unit": "m",
            "quantity": 5,
            "quantity_unit": "cbm",
            "container_size": "20ft",
            "expected_rate": 450,
            "expected_rate_currency": "USD"
        }
    ]
}

resp = httpx.post(f"{BASE}/rfq", json=rfq_payload, timeout=15)
result = check(resp, "Step 1 — RFQ Created")
rfq_id = result["rfq_id"]
print(f"\n  RFQ ID: {rfq_id}")

# ── Step 2: Dispatch to suppliers ─────────────────────────────────────────────
print(f"\n► Step 2: Dispatch RFQ to suppliers")
resp = httpx.post(f"{BASE}/rfq/{rfq_id}/dispatch", timeout=30)
check(resp, "Step 2 — Dispatched to Suppliers")

# ── Check RFQ status ──────────────────────────────────────────────────────────
print(f"\n► Check RFQ status")
resp = httpx.get(f"{BASE}/rfq/{rfq_id}", timeout=10)
check(resp, "RFQ Status")

# ── Step 3: Simulate supplier responses via webhook ───────────────────────────
print(f"\n► Step 3: Simulate supplier responses (via webhook)")

supplier_replies = [
    {
        "rfq_id": rfq_id,
        "from": "sales@kayujati.co.id",
        "to": "pav@instructset.com",
        "subject": f"Re: RFQ {rfq_id}",
        "text": (
            "Dear Pav,\n\n"
            "Thank you for your enquiry. Our prices are as follows:\n\n"
            "1. Teak Timber Grade A: USD 780 per CBM. "
            "Dimensions 2.4m x 0.1m x 0.05m. Lead time: 21 days.\n\n"
            "2. Meranti Plywood AB Grade: USD 420 per CBM. "
            "18mm thick. Lead time: 14 days.\n\n"
            "FOB Tanjung Priok. Min order 5 CBM.\n\n"
            "Best regards,\nBudi Santoso\nPT Kayu Jati Nusantara"
        ),
    },
    {
        "rfq_id": rfq_id,
        "from": "export@borneoTimber.id",
        "to": "pav@instructset.com",
        "subject": f"Re: RFQ {rfq_id}",
        "text": (
            "Hello,\n\n"
            "We can supply the following:\n\n"
            "Teak Grade A: $820/CBM, 18 days lead time.\n"
            "Meranti Plywood AB: $400/CBM, 10 days lead time.\n\n"
            "FOB Balikpapan.\n\n"
            "Regards,\nBorneo Timber Export Co"
        ),
    },
]

for i, reply in enumerate(supplier_replies, 1):
    resp = httpx.post(
        f"{BASE}/webhooks/email",
        json=reply,  # JSON body — no python-multipart dependency needed
        timeout=15,
    )
    if resp.status_code == 200:
        print(f"   ✅ Supplier {i} response ingested ({reply['from']})")
    else:
        print(f"   ❌ Supplier {i} webhook failed [{resp.status_code}]: {resp.text}")

# ── Check status after responses ─────────────────────────────────────────────
resp = httpx.get(f"{BASE}/rfq/{rfq_id}", timeout=10)
check(resp, "RFQ Status After Responses")

# ── Step 5: Generate buyer quote ──────────────────────────────────────────────
print(f"\n► Step 5: Generate buyer quote (best price + 3% markup)")
quote_payload = {
    "freight_usd": 1200,
    "freight_origin_port": "Tanjung Priok",
    "freight_destination_port": "Nhava Sheva",
    "freight_container_size": "20ft",
    "freight_logistics_partner": "Maersk"
}
resp = httpx.post(f"{BASE}/rfq/{rfq_id}/quote", json=quote_payload, timeout=15)
quote = check(resp, "Step 5 — Buyer Quote Generated")

# ── Step 6: Test negotiation ──────────────────────────────────────────────────
print(f"\n► Step 6: Buyer counter-offer (negotiation round 1)")
neg_payload = {"target_rate": 390, "currency": "USD"}
resp = httpx.post(f"{BASE}/rfq/{rfq_id}/negotiate", json=neg_payload, timeout=15)
check(resp, "Step 6 — Negotiation Round 1")

# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  ✅ Full workflow test complete!")
print(f"  RFQ ID : {rfq_id}")
print(f"  Check Airtable → RFQs table to see the persisted record.")
print(f"  Check pav@instructset.com for dispatched RFQ emails.")
print(f"{'='*50}\n")
