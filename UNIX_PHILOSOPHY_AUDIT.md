# Unix Philosophy Audit Report — Rent Reconciliation

**Date:** 2026-02-03  
**Scope:** `rent-reconciliation` codebase (parsers, reconciliation, validation, database, app, templates)

---

## 1. Executive Summary

The rent-reconciliation system shows **good modularity and clear separation** in its core: parsers (PDF, SMS), matcher, state machine, and database are in distinct modules with well-defined interfaces. **Violations** include: duplicate logic (balance checksum in both `pdf_parser.py` and `src/validation/checksum.py`; verification logic duplicated between `app.py` and the reconciliation matcher/state machine), **silent failure** via bare `except:` in parsers, and a **monolithic app.py** that mixes policy, mechanism, and UI. **Recommendation:** Unify verification on the matcher/state machine, remove duplicate checksum code, replace bare `except:` with fail-loud handling, and extract a small “engine” layer so the web app is a thin UI over it.

---

## 2. Principle-by-Principle Evaluation

### Modularity
**Rating:** ✅ Good  
**Evidence:**  
- `src/parsers/pdf_parser.py` and `src/parsers/sms_parser.py` are separate; each exposes a single main entry (`parse_bank_statement`, `parse_mpesa_message`).  
- `src/reconciliation/matcher.py` and `state_machine.py` are separate; matcher does matching only, state machine does persistence and transitions.  
- `src/database/db.py` + `schema.sql`: connection and schema are separate; `get_connection()` is the single interface.  
**Recommendation:** Keep this structure. Consider moving `Transaction`/`SMSClaim` to a small `src/models.py` or keep in parsers if you want parsers to own their output types.

---

### Clarity
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Parsers are clear: docstrings describe inputs/outputs (e.g. `pdf_parser.py` lines 34–41, 579–587).  
- `app.py` is long (~530 lines) with inline SQL and mixed concerns; `_audit_display_details` (lines 46–116) is a large helper that mixes display logic and DB lookups.  
- Two verification paths: `app.py` does its own “match by mpesa_ref” in `verify_payments` (lines 419–451); `demo_sms_verification.py` uses `match_sms_to_bank` + `ClaimStateMachine`. Intent (web vs demo) is not documented.  
**Recommendation:** Add a short ARCHITECTURE.md describing web flow vs CLI/demo flow. Refactor `_audit_display_details` into a small module or at least document the “rich details” format.

---

### Composition
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Parsers compose well: `parse_bank_statement` uses `extract_raw_text` → `segment_transactions` → `parse_transaction` → `validate_balance_checksum`.  
- **App does not compose** with reconciliation: it never imports `matcher` or `state_machine`; it reimplements matching in SQL (`verify_payments`).  
- `src/validation/checksum.py` exists but is **never imported**; `pdf_parser` implements the same checksum internally (lines 552–612, 1009–1012). So two implementations, one unused.  
**Recommendation:** Use one verification path. Prefer: app calls matcher + state machine (or a thin service that uses them), and persist to DB from there. Use `src/validation/checksum.py` from `pdf_parser` (or delete the duplicate).

---

### Separation (policy vs mechanism, UI vs engine)
**Rating:** ❌ Violation  
**Evidence:**  
- **UI vs engine:** All business logic in `app.py` lives inside route handlers: DB reads/writes, parsing, verification, and flash messages are interleaved. There is no “engine” API (e.g. `reconcile.verify_claim(claim_id, statement_id)`).  
- **Policy vs mechanism:** Upload policy (e.g. “reject if checksum invalid”) is in the route (lines 364–369); the mechanism (parse + validate) is in the parser. That part is okay. But “how we verify payments” (by ref only, no amount check in web path) is policy buried in app code.  
**Recommendation:** Introduce a small `src/reconciliation/service.py` (or similar) that exposes `verify_payments_for_statement(statement_id)`, `report_payment(property_id, message, unit_id)`, etc., and have `app.py` only call that and render templates. Move “first property only” policy to config or a single helper.

---

### Simplicity
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Parser pipeline is conceptually simple (text → segments → transactions → validate).  
- `pdf_parser.extract_amount` and `_complete_amount` are complex (many branches and regexes) but necessary for the PDF format.  
- App is not simple: one file does setup, units, tenants, claims, upload, verify, assign, charges, activity, review.  
**Recommendation:** Split app into blueprints or at least logical groups (property_setup, units_tenants, payments, statements, review). Keep parser complexity as-is but add a one-paragraph “PDF amount extraction strategy” in the module docstring.

---

### Parsimony
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- **Duplication:** `validate_balance_checksum` in both `src/parsers/pdf_parser.py` (552–612) and `src/validation/checksum.py` (13–68). Same logic, two places.  
- **Duplication:** Verification logic: matcher + state machine in demo vs inline SQL in app.  
- No obvious “golden” data format shared by parser output and DB; `Transaction` fields are mapped ad hoc in app (e.g. lines 401–417).  
**Recommendation:** Single implementation of balance checksum; single verification path. Consider a single canonical representation (e.g. dict or dataclass) for “payment/transaction” that both parser output and DB can use.

---

### Transparency
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Parsers return structured results with `validation`, `summary`, `errors`, `parse_warnings` — good.  
- State machine uses `print()` for load/save warnings (e.g. `state_machine.py` lines 83, 97) instead of logging or return codes.  
- App uses `flash()` for user feedback but no structured logging for debugging (e.g. why a claim didn’t match).  
**Recommendation:** Replace `print` in state machine with `logging` or return warnings in the result dict. Add minimal logging in app for verification (e.g. “verified N claims for statement X”).

---

### Robustness
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- **Bare `except:`** in `pdf_parser.py` (e.g. lines 224, 239, 253, 281, 290, 308, 321, 348, 403, 419, 425, 442, 448, 466, 472, 480, 718, 735, 847, 863, 886) and `sms_parser.py` (e.g. 79, 88, 98, 118, 133, 149). These swallow all exceptions (including `KeyboardInterrupt`, `SystemExit`) and hide real errors.  
- DB layer is robust: `get_connection()` uses context manager, commit/rollback, and closes connection.  
- Parser entry point catches exception and returns a result dict with `success: False` (e.g. `pdf_parser.py` 658–666) — good for callers, but internal helpers fail silently.  
**Recommendation:** Replace bare `except:` with `except Exception:` and either re-raise after logging or return a sentinel/error in a structured way. In parsers, prefer “no match” returns (None, [], etc.) and let the top-level `parse_*` catch only expected failures and set `errors`.

---

### Representation (“data dominates”)
**Rating:** ✅ Good  
**Evidence:**  
- `Transaction` and `SMSClaim` dataclasses clearly define the data; parsing fills them.  
- `schema.sql` defines tables and a view (`unit_balances`); business rules (e.g. balance = charged - paid) live in the view.  
- State machine stores claims as JSON keyed by reference; structure is simple.  
**Recommendation:** Keep data-centric design. If you add more flows, prefer new tables/views and dataclasses over branching logic in code.

---

### Least Surprise
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Naming is consistent: `parse_bank_statement`, `parse_mpesa_message`, `match_sms_to_bank`, `add_claim`.  
- **Surprise:** Web verification does not use `match_sms_to_bank` or amount mismatch status; it only checks ref existence. So “verified” in the app means “ref found,” not “ref + amount match.”  
- **Surprise:** `init_database()` runs at import time in `app.py` (line 25); first request can be slower and failures happen at startup.  
**Recommendation:** Document “verified = ref matched (amount optional)” in UI or docstring. Consider running `init_database()` explicitly (e.g. CLI or startup script) rather than at module load. Optionally add amount check in web verification and surface AMOUNT_MISMATCH.

---

### Silence
**Rating:** ❌ Violation  
**Evidence:**  
- “Silence” in Unix sense: successful programs don’t clutter output. Here, **silent failure** is the issue.  
- Bare `except:` blocks produce no message when something fails; callers only see missing or wrong data.  
- State machine `print()` on load/save errors (lines 83, 97) is the opposite of silence for success — it’s the only feedback for failure.  
**Recommendation:** Be loud on failure: log or return errors. Be silent on success in library code; let the app or CLI decide what to print.

---

### Repair (fail fast, fail loud)
**Rating:** ❌ Violation  
**Evidence:**  
- Parsers often **do not** fail fast: they catch all exceptions and return None or continue (e.g. `pdf_parser.py` many `except:` in `extract_amount`, `_complete_amount`, `extract_balances`).  
- Top-level `parse_bank_statement` does fail loud for critical error: it returns `success: False` and `errors` when validation fails or exception occurs (lines 636, 658–666).  
- App: validation failure on upload aborts and flashes error (lines 364–369) — good. But parsing exceptions are caught and flashed generically (416–419); no distinction between “file missing” vs “parse bug.”  
**Recommendation:** In parser helpers, avoid catching broad exceptions; let them propagate or catch only specific ones (e.g. `ValueError`, `DecimalException`) and re-raise or attach to a “parse_warnings”/“errors” list. Ensure one place (e.g. `parse_bank_statement`) decides “fail loud” (return success=False + errors) for invalid input.

---

### Economy (dev time vs cleverness)
**Rating:** ✅ Good  
**Evidence:**  
- Straightforward use of SQLite, Flask, Jinja2, pdfplumber; no custom frameworks.  
- Parsers use regex and simple state (e.g. date_pattern for segments); no over-engineered parsing.  
- IDs are simple: `generate_id('PROP')` etc.  
**Recommendation:** Stay the course. Avoid adding generic “plugin” or “strategy” frameworks until you have a second bank or second message format.

---

### Generation (codegen vs hand hacks)
**Rating:** N/A (no codegen in scope)  
**Evidence:**  
- No generated code. Schema and migrations are hand-written.  
**Recommendation:** If you add migrations later, consider a simple linear migration runner (e.g. `migrations/001_initial.sql`) rather than codegen.

---

### Optimization (did we over-optimize?)
**Rating:** ✅ Good  
**Evidence:**  
- No caching, no async, no custom indexes beyond what’s in schema. Queries are simple.  
- Parser does full scan of text/lines; acceptable for statement-sized PDFs.  
**Recommendation:** Don’t optimize further until you have concrete performance requirements.

---

### Diversity (no “one true way” assumptions)
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- **Single-property assumption:** App assumes “first property” everywhere (`SELECT * FROM properties LIMIT 1`). No property_id in URL or session.  
- **Single storage:** State machine uses JSON file; app uses SQLite. Demo uses file state; web uses DB. So “claims” exist in two possible places depending on path.  
**Recommendation:** Document “single property” as a deliberate scope. If you later support multiple properties, introduce property context (e.g. path or session) and avoid hardcoding LIMIT 1. For claims, unify on DB and optionally phase out file-based state machine or use it only for CLI.

---

### Extensibility
**Rating:** ⚠️ Needs Work  
**Evidence:**  
- Adding a new bank format would require a new parser module and possibly a new “transaction” type; no shared parser interface (e.g. `parse_statement(path) -> List[Transaction]`).  
- Adding a new message format is easy: extend `sms_parser` patterns and `detect_format`.  
- Schema is extensible: new tables/columns don’t break existing code; view `unit_balances` isolates balance logic.  
**Recommendation:** Define a minimal “statement parser” interface (e.g. returns list of transactions + validation result) so the app can call “any” parser. Keep SMS parser as-is for now.

---

## 3. Component Analysis

| Component | One thing well? | Clean interfaces? | Easy to connect? | How it fails |
|-----------|-----------------|-------------------|------------------|---------------|
| **pdf_parser** | Yes: extract and validate bank statement from PDF | Yes: `parse_bank_statement(path)` → dict with success, transactions, validation, errors | Yes: pure functions, file path in, dict out; can be used by CLI or app | Loud at top level (success=False, errors); silent inside (bare except) |
| **sms_parser** | Yes: parse Mpesa message into reference + optional amount/timestamp | Yes: `parse_mpesa_message(text)` → dict; `create_sms_claim(parsed)` → SMSClaim | Yes: no I/O, easy to test | Silent in helpers (bare except); loud when reference missing (error in dict) |
| **matcher** | Yes: match one SMS claim to bank transactions by ref (and amount) | Yes: `match_sms_to_bank(sms_claim, bank_transactions)` → dict | Yes: in-memory only; not used by app currently | N/A (deterministic) |
| **state_machine** | Yes: persist claims, dedupe, status transitions | Yes: `ClaimStateMachine(state_file)`, `add_claim`, `get_claim`, etc. | Partially: file-based; app uses DB instead, so two storage paths | Prints warning on load/save error; mutates claim in place |
| **checksum** | Yes: validate opening + credits - debits = closing | Yes: `validate_balance_checksum(transactions, opening, closing)` → dict | Yes: but unused; pdf_parser has its own copy | Loud (returns valid: False, error message) |
| **db** | Yes: connection and schema init | Yes: `get_connection()`, `init_database()`, `generate_id(prefix)` | Yes: context manager, raw SQL | Loud (rollback + raise on exception) |
| **app** | No: many concerns (setup, units, tenants, claims, upload, verify, assign, charges, activity, review) | No: only HTTP routes; no programmatic API | Hard: logic tied to Flask request/response | Flash messages; some paths catch Exception and flash generic error |
| **templates** | Yes: each template one page/section; base extends | Yes: Jinja2 blocks, url_for, get_flashed_messages | Yes: standard Flask pattern | N/A |

---

## 4. Data Structures Review

- **Transaction / SMSClaim:** Simple dataclasses with clear fields; optional fields where appropriate. Good.
- **Parser output:** Dict with `success`, `transactions`, `validation`, `summary`, `errors` — consistent and easy to consume.
- **Schema:** Normalized tables (properties, units, tenants, statements, transactions, claims, payments, charges, audit_log). View `unit_balances` encodes “balance = charged - paid” in one place — **data dominates**.
- **State machine:** In-memory dict of reference → claim; serialized to JSON. Simple; no schema drift.
- **Weak spot:** No single canonical “payment record” type shared by parser output and DB rows; app maps `Transaction` attributes to columns ad hoc. Consider a small mapping layer (e.g. `transaction_to_row(txn)`) in one place.

---

## 5. Violations Table

| File:Line(s) | Principle | Severity | Suggested fix |
|--------------|-----------|----------|----------------|
| `src/parsers/pdf_parser.py` (many) | Repair / Silence | High | Replace `except:` with `except Exception:` and either re-raise or append to errors; avoid swallowing. |
| `src/parsers/sms_parser.py` 79, 88, 98, 118, 133, 149 | Repair / Silence | High | Same as above. |
| `src/reconciliation/state_machine.py` 83, 97 | Transparency / Silence | Medium | Use logging or return failure in result; no `print()`. |
| `app.py` (whole file) | Separation (UI vs engine) | High | Extract engine/service layer; routes only call service + render. |
| `app.py` 419–451 vs `matcher.py` | Composition / Parsimony | High | Use `match_sms_to_bank` (or a service that uses it) in app; persist from one place. |
| `pdf_parser.py` 552–612 vs `src/validation/checksum.py` | Parsimony | Medium | Keep one implementation; have pdf_parser call `validation.checksum` or remove validation/checksum.py. |
| `app.py` 25 | Least Surprise | Low | Run `init_database()` from a startup hook or CLI, not at import. |

---

## 6. Good Practices Table

| Practice | Where |
|----------|--------|
| Single entry points for parsers | `pdf_parser.parse_bank_statement`, `sms_parser.parse_mpesa_message` |
| Structured result dicts with success/errors | `parse_bank_statement`, `parse_mpesa_message`, `match_sms_to_bank`, `validate_balance_checksum` |
| Balance checksum before trusting parser | `pdf_parser.py` 1009–1012; app rejects upload if invalid (364–369) |
| DB connection as context manager | `src/database/db.py` `get_connection()` |
| Schema in separate file | `src/database/schema.sql` |
| View for derived data | `unit_balances` in schema.sql |
| Docstrings on main functions | Parsers, matcher, state machine, db |
| CLI entry for parser | `pdf_parser.py` `if __name__ == '__main__'` |
| Idempotent schema (CREATE IF NOT EXISTS) | schema.sql |
| Foreign keys and indexes | schema.sql (PRAGMA foreign_keys, indexes on mpesa_ref, statement_id, etc.) |

---

## 7. Questions for Other AIs

- **Claude (architecture/docs):** “We have two verification paths: web app matches by `mpesa_ref` in SQL and never uses `matcher.match_sms_to_bank` or `ClaimStateMachine`, while the demo script uses both. What’s the cleanest way to document and unify this so that one ‘source of truth’ (e.g. matcher + DB) is used everywhere, and how would you structure a short ARCHITECTURE.md?”
- **DeepSeek (implementation/refactor):** “The PDF parser has many bare `except:` blocks that hide bugs. How would you refactor `extract_amount` and `_complete_amount` to fail fast where appropriate (e.g. invalid Decimal) while still returning None for ‘no amount found’ without catching BaseException?”
- **ChatGPT (safety/UX):** “In the web flow we mark a payment as ‘verified’ when the claim’s mpesa_ref appears in the bank statement, but we don’t check amount mismatch (unlike the matcher which sets AMOUNT_MISMATCH). Should we add amount check and show a warning/block when SMS amount and bank amount differ, and how would you surface that in the UI?”
- **Grok (data validation/edge cases):** “The SMS parser accepts ‘reference only’ and leaves amount as None; the matcher then treats that as ‘amount match = True’ when SMS has no amount. What edge cases (e.g. duplicate ref with different amounts, or ref in multiple statements) should we explicitly validate and where (parser, matcher, state machine, or DB)?”
- **Gemini (alternative approaches/simplification):** “Could we simplify the system by (1) removing the file-based ClaimStateMachine and doing all claim state in the DB (payment_claims + payments), and (2) making the PDF parser the only place that implements balance checksum (and deleting src/validation/checksum.py)? What would we lose or gain?”

---

## 8. Summary Table

| Principle | Status | Priority to fix |
|-----------|--------|------------------|
| Modularity | ✅ Good | — |
| Clarity | ⚠️ Needs Work | Medium |
| Composition | ⚠️ Needs Work | High |
| Separation (policy/mechanism, UI/engine) | ❌ Violation | High |
| Simplicity | ⚠️ Needs Work | Medium |
| Parsimony | ⚠️ Needs Work | High (dedupe checksum + verification) |
| Transparency | ⚠️ Needs Work | Low |
| Robustness | ⚠️ Needs Work | High (bare except) |
| Representation | ✅ Good | — |
| Least Surprise | ⚠️ Needs Work | Low |
| Silence | ❌ Violation | High (with Repair) |
| Repair | ❌ Violation | High |
| Economy | ✅ Good | — |
| Generation | N/A | — |
| Optimization | ✅ Good | — |
| Diversity | ⚠️ Needs Work | Low (document single-property) |
| Extensibility | ⚠️ Needs Work | Low |

---

## 9. Final Verdict

- **Score: 6/10** for Unix adherence. Strong in modularity, data design, and economy; weak in separation of UI and engine, single verification/checksum path, and fail-fast/silence handling.
- **Top 3 actions before growing the system:**
  1. **Unify verification and checksum:** Use one verification path (matcher + optional state machine or DB) for both web and demo; use one implementation of balance checksum (e.g. `src/validation/checksum.py`) and call it from the PDF parser. Remove duplication.
  2. **Fix silent failure:** Replace all bare `except:` in parsers with specific exception handling or `except Exception` plus explicit error reporting; ensure failures are visible (log or return in result), and success paths stay quiet.
  3. **Extract an engine layer:** Move business logic out of `app.py` into a small service (e.g. `reconcile.verify_payments_for_statement`, `reconcile.report_payment`). Keep routes thin: call engine, then render template and flash. This improves separation, testability, and composition.

---

*End of Unix Philosophy Audit.*
