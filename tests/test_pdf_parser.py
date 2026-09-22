#!/usr/bin/env python3
"""
Regression tests for the Co-operative / Family Bank statement parser.

Every case here is a defect that reached production and was found only by
reconciling real statements by hand. The fixtures are synthetic — the repository
is public, so no real statement text, payer name or reference appears — but each
reproduces the exact line shape that broke the parser.

No test framework required:

    ./venv/bin/python tests/test_pdf_parser.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.pdf_parser import (  # noqa: E402
    extract_balances,
    extract_dates,
    parse_transaction,
    reconcile_amounts_from_balances,
    segment_transactions,
)


def blocks(raw_text):
    lines = raw_text.strip().split('\n')
    return segment_transactions('\n'.join(lines), [1] * len(lines))


def parse_all(raw_text):
    return [parse_transaction(b, b.get('page_number', 1)) for b in blocks(raw_text)]


# A credit large enough that the amount wraps onto the following lines, which is
# the layout the parser was originally written against.
WRAPPED = """01-JUL- 01-JUL-2026 Paybill Credit From Paybill: 120,000.00 014
2026 222189, Ref : 20,000.0
UAAAAAAAAA, Sender: 0
TEST PAYER
ALPHA BETA ,
Narration: MOWIN
014000050518 MOWIN
GAS SUPPLY"""

# A credit small enough to print entirely on the first line, sharing that line
# with the running balance. No amount wraps, so the "Ref :" lookahead finds
# nothing and the fallback — which only scans lines at or after "Ref" — never
# sees it either.
UNWRAPPED_SMALL = """02-JUL- 02-JUL-2026 Paybill Credit From Paybill: 100.00 120,100.00 014
2026 222189, Ref :
UBBBBBBBBB, Sender:
TEST PAYER
GAMMA DELTA ,
Narration: MOWIN
014000050518 MOWIN
GAS SUPPLY"""


def test_small_amount_recovered_from_balance():
    """A credit under KES 1,000 must survive. It used to become a PARSE_ERROR,
    so the reference never reached bank_transactions, the tenant's claim was
    flagged for a missing reference, and tenants.flagged was set permanently."""
    txns = parse_all(WRAPPED + '\n' + UNWRAPPED_SMALL)
    assert len(txns) == 2, f'expected 2 transactions, got {len(txns)}'
    reconcile_amounts_from_balances(txns, Decimal('100000.00'))

    assert txns[0].amount == Decimal('20000.00'), txns[0].amount
    small = txns[1]
    assert small.amount == Decimal('100.00'), f'small credit lost: {small.amount}'
    assert small.direction == 'credit', small.direction
    assert small.txn_type == 'PAYBILL_CREDIT', small.txn_type


def test_balance_movement_overrides_a_wrong_amount():
    """The running-balance column is the bank's own arithmetic, so it wins."""
    txns = parse_all(WRAPPED)
    txns[0].amount = Decimal('999999.00')          # pretend the regex misread it
    reconcile_amounts_from_balances(txns, Decimal('100000.00'))
    assert txns[0].amount == Decimal('20000.00'), txns[0].amount
    assert any('balance movement' in w for w in txns[0].parse_warnings)


def test_broken_balance_chain_leaves_neighbours_alone():
    """A transaction with no readable balance must not cause the next one to be
    reconciled against a stale balance."""
    txns = parse_all(WRAPPED)
    txns[0].running_balance = Decimal('0.00')
    reconcile_amounts_from_balances(txns, Decimal('100000.00'))
    assert txns[0].amount == Decimal('20000.00'), txns[0].amount


def test_closing_balance_prefers_value_over_clear():
    """The ledger counts up to VALUE BALANCE. CLEAR BALANCE excludes uncleared
    items and sat 390.00 below it, so every upload failed its checksum."""
    _, closing = extract_balances(
        '*OPENING BALANCE* 100,000.00\n'
        'CLEAR BALANCE 517,180.28\n'
        'VALUE BALANCE 517,570.28\n'
        'UNCLEAR BALANCE 0.00'
    )
    assert closing == Decimal('517570.28'), closing


def test_unit_hint_ignores_the_account_number():
    """'MOWIN 014000050518' is the account number, not unit 0140. This produced
    a bogus Tier-1 hint on 63 of 67 credits in one statement."""
    txn = parse_all(WRAPPED)[0]
    assert txn.unit_hint is None, f'account number read as unit {txn.unit_hint!r}'


def test_unit_hint_still_reads_a_real_unit():
    raw = WRAPPED.replace('Narration: MOWIN\n014000050518 MOWIN',
                          'Narration: MOWIN 5D\n014000050518 MOWIN')
    assert parse_all(raw)[0].unit_hint == '5D'


def test_sender_name_spans_wrapped_lines():
    """Names wrap and end at a comma or the Narration label. Reading only the
    first line truncated them to two tokens, which starved the >=2-token overlap
    test in enrich_with_suggestions() and left real payments unassignable."""
    txn = parse_all(WRAPPED)[0]
    assert txn.sender == 'TEST PAYER ALPHA BETA', repr(txn.sender)


def test_sender_stops_at_narration_on_a_shared_line():
    raw = WRAPPED.replace('ALPHA BETA ,\nNarration: MOWIN', "ALPHA BETA , Narration: MOWIN")
    assert parse_all(raw)[0].sender == 'TEST PAYER ALPHA BETA', repr(parse_all(raw)[0].sender)


def test_year_must_look_like_a_year():
    """The year is taken from the continuation line. Accepting any four digits
    let an account or cheque number stand in as the year, which is how a
    statement came to be stored with period_start '0034-02-02'."""
    good, _ = extract_dates('01-JUL- 01-JUL- Paybill Credit', '2026 222189, Ref : 1,000.0')
    assert good == '01-JUL-2026', good

    bad, _ = extract_dates('02-FEB- 02-FEB- Paybill Credit', '0034000505 222189, Ref : 1,000.0')
    assert bad is None or '0034' not in bad, f'nonsense year accepted: {bad!r}'


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failures = []
    for test in tests:
        try:
            test()
            print(f'  PASS  {test.__name__}')
        except AssertionError as exc:
            failures.append((test.__name__, exc))
            print(f'  FAIL  {test.__name__}: {exc}')
        except Exception as exc:  # noqa: BLE001
            failures.append((test.__name__, exc))
            print(f'  ERROR {test.__name__}: {type(exc).__name__}: {exc}')
    print(f'\n{len(tests) - len(failures)}/{len(tests)} passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
