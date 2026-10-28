"""Meter evidence for billed water, scoped to the tenant's own charges.

Older amount-only uploads have no readings. Never infer readings from an amount
or today's property rate: only a single, matching recorded calculation is shown.
"""

import math


def water_breakdowns(conn, tenant_id, property_id):
    rows = conn.execute("""
        SELECT rc.id AS charge_id, rc.amount AS charged, wr.previous_reading,
               wr.current_reading, wr.units_consumed, wr.rate, wr.amount,
               wu.reading_period, wu.charge_period
        FROM rent_charges rc
        JOIN water_uploads wu ON wu.property_id = rc.property_id AND wu.charge_period = rc.period
        JOIN water_readings wr ON wr.upload_id = wu.id AND wr.unit_id = rc.unit_id
        WHERE rc.tenant_id = ? AND rc.property_id = ? AND rc.charge_type = 'water'
        ORDER BY rc.id, wr.id
    """, (tenant_id, property_id)).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['charge_id'], []).append(dict(row))
    result = {}
    for charge_id, candidates in grouped.items():
        detail = {'available': False, 'reason': 'inconsistent'}
        if len(candidates) == 1:
            r = candidates[0]
            try:
                prev, curr, used, rate, amount, charged = (
                    float(r[k]) for k in ('previous_reading', 'current_reading',
                                         'units_consumed', 'rate', 'amount', 'charged'))
                if (all(math.isfinite(v) and v >= 0 for v in (prev, curr, used, rate, amount, charged))
                        and math.isclose(curr - prev, used, rel_tol=0, abs_tol=0.000001)
                        and math.isclose(used * rate, amount, rel_tol=0, abs_tol=0.005)
                        and math.isclose(amount, charged, rel_tol=0, abs_tol=0.005)):
                    detail = dict(r, available=True)
            except (TypeError, ValueError):
                pass
        result[charge_id] = detail
    return result
