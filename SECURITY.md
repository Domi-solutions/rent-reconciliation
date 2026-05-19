# Domi Security Architecture

## What This Document Is

Two audiences:

**Section 1 — Owner Talking Points**: Plain English. Use this when an owner asks "how do I know Domi will protect my money?" No jargon.

**Section 2 — Technical Implementation**: For developers and AI agents. Links every guarantee to the code that enforces it.

For the full threat model and agent-readable registry, see `.agent/security.yaml`.

---

## Section 1 — Owner Talking Points

### "How do I know Domi won't steal my money?"

Use these points in order of impact:

---

**Your payout account is set by you — not by the agency.**

Your M-Pesa number for receiving rent is registered through your Domi owner portal, not entered by your property manager. We send a verification code directly to the number you provide, proving you own that SIM. The agency has no write access to this field — ever.

---

**There's a 48-hour security hold after any payout number change.**

Even after you verify a new payout number with your code, disbursements don't move immediately. There's a 48-hour window where you can cancel if something looks wrong. This stops any rushed misdirection of funds.

---

**If your contact details change, your old number gets an immediate SMS.**

If anyone changes the phone number on your Domi profile, your previous number gets a text immediately: "Your Domi contact number was updated. If you did not request this, call Domi support now." This breaks the most common redirect-and-intercept attack before it can succeed.

---

**You get a direct SMS from Domi when anyone is added to or removed from your property.**

Any change to property ownership triggers a notification delivered by Domi's infrastructure — not routed through the agency's messaging. If you're removed from a property you own, you'll know within seconds. If a new owner is added without your knowledge, you'll know immediately.

---

**Two registered payout accounts on one property = automatic freeze.**

If two different payout accounts are ever confirmed for the same property, all disbursements halt automatically and a platform alert fires. No money moves until Domi resolves the conflict. This eliminates the "add a rogue co-owner" attack entirely.

---

**The agency cannot see your M-Pesa number.**

Your payout M-Pesa number is never shown in the agency admin interface — not in any list, not in any HTML source. The agency can only see whether a payout account is confirmed or not. Your actual number is visible only to you, in your owner portal, after you log in.

---

**Every reversal of a tenant payment triggers an SMS to the tenant.**

If a payment recorded against a tenant's account is reversed or corrected, the tenant gets an SMS. Tenants can flag unexpected reversals directly to Domi. This makes payment misdirection detectable from the tenant side, independently of the agency.

---

**Domi maintains a write-once audit trail the agency cannot edit.**

Every sensitive action — payout changes, ownership changes, payment reversals, rent adjustments — is logged to a platform audit trail that the agency has no access to. They cannot edit or delete it. Domi uses this to independently monitor for patterns.

---

## Section 2 — Technical Implementation

### Threat model overview

The core risk is not technical vulnerabilities — it's legitimate admin capabilities being misused. An agency admin has CRUD access to most of the system. The defenses give property owners an independent, out-of-band channel (Domi platform, not the agency) to observe anything the admin does that affects them.

### Defense status

| Defense | Threat | Status | Key file |
|---|---|---|---|
| D1 — SMS on owners.phone change | T1: Phone substitution attack | ✅ Implemented | `app.py` `edit_owner` |
| D2 — Lock owner password after first login | T2: Credential takeover | ⏳ Sprint 2 | `app.py` `set_owner_password` |
| D3 — Ownership changes notify all owners | T3: Silent removal, T4: Rogue co-owner | ✅ Implemented | `app.py` assign/remove/delete routes |
| D4 — Two confirmed payout owners = hard block | T4: Rogue co-owner | ✅ Implemented | `src/payments/disbursements.py` |
| D5 — Management fee rate change guard | T5: Fee manipulation | ⏳ Sprint 2 | No property settings UI yet |
| D6 — Owner activity tab (shadow log view) | T1–T5: General visibility | ⏳ Sprint 2 | `src/routes/viewer_routes.py` |
| D7 — payout_mpesa never in admin queries | T10: Number exposure | ✅ Implemented | `app.py` `manage_owners` |
| D8 — Cumulative rent drift detection | T6: Gradual skimming | ⏳ Sprint 3 | `src/agent/detector.py` |
| D9 — SMS to tenant on payment reversal | T7: Payment misdirection | ✅ Implemented | `app.py` `delete_payment`, `correct_payment` |

### Payout account security model (most important)

The payout security is a separation-of-control model:

- `owners.phone` — contact phone; admin-writable, but any change SMSes the old number
- `owners.payout_mpesa` — disbursement destination; **owner-write-only**, admin has zero write path
- OTP flow: owner submits number → SMS sent to that number (proves SIM ownership) → enters code → 48h hold → active
- `_get_confirmed_payout_owner()` in `disbursements.py` enforces: `payout_confirmed=1` AND `payout_active_at <= now()` AND count must be exactly 1

A fraudulent owner created by a rogue admin cannot receive funds without also controlling the target M-Pesa SIM.

### Platform shadow log

`platform_shadow_log` is written by `src/platform/guardian.py`. It is:
- Never visible in any admin or owner route
- Only readable at `/platform/shadow-log` (platform admin only)
- Written on: unit rent/service change, owner removal, owner assignment, owner phone change, payment reversal, tenant move-out, management fee change

Agents: call `platform_log()` from `src/platform/guardian.py` for any sensitive action. Do not write directly to the table.

### Sprint roadmap

**Sprint 1 — before disbursements go live (complete as of 2026-05-19):**
- D1, D3, D4, D7, D9

**Sprint 2 — before onboarding external owners:**
- D2: Lock owner password write path after first login
- D5: Management fee rate change guard (when property settings UI is built)
- D6: Owner activity tab at `/view/<property_id>/activity`

**Sprint 3 — Phase 5 intelligence:**
- D8: Cumulative rent drift detection in `src/agent/detector.py`
