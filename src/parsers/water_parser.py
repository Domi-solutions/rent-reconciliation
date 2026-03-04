"""Water readings Excel parser."""
import pandas as pd
from decimal import Decimal
from typing import Optional
import re

from src.parsers.excel_parser import parse_currency


def normalize_water_column(col: str) -> str:
    """Normalize water readings column names."""
    col = col.lower().strip().replace('\n', ' ').replace('\r', ' ')
    col = re.sub(r'\s+', ' ', col)
    mappings = {
        'house no': 'unit_number',
        'house no.': 'unit_number',
        'unit no': 'unit_number',
        'unit no.': 'unit_number',
        'unit number': 'unit_number',
        'unit': 'unit_number',
        'tenant name': 'tenant_name',
        'tenant': 'tenant_name',
        'name': 'tenant_name',
        'water charge': 'water_charge',
        'water charge (kes)': 'water_charge',
        'water': 'water_charge',
        'water amount': 'water_charge',
        'amount': 'water_charge',
    }
    return mappings.get(col, col.replace(' ', '_'))


def parse_water_excel(file_path: str) -> dict:
    """
    Parse Excel file with water charge readings.
    Returns:
        {
            'success': bool,
            'rows': [{'unit_number': str, 'tenant_name': str, 'water_charge': float}, ...],
            'errors': list,
            'warnings': list,
            'total_water_charges': float
        }
    """
    errors = []
    warnings = []
    rows = []
    total = Decimal('0')

    # Auto-detect header row
    df = None
    header_row_used = 0
    for header_row in [0, 1, 2]:
        try:
            candidate = pd.read_excel(file_path, header=header_row)
            cols = [normalize_water_column(str(c)) for c in candidate.columns]
            if 'unit_number' in cols:
                df = candidate
                header_row_used = header_row
                break
        except Exception:
            continue

    if df is None:
        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            return {'success': False, 'rows': [], 'errors': [f'Failed to read Excel file: {e}'], 'warnings': [], 'total_water_charges': 0.0}

    if df.empty:
        return {'success': False, 'rows': [], 'errors': ['Excel file is empty'], 'warnings': [], 'total_water_charges': 0.0}

    if header_row_used > 0:
        warnings.append(f'Header row detected at row {header_row_used + 1}')

    df.columns = [normalize_water_column(str(c)) for c in df.columns]

    required = ['unit_number', 'water_charge']
    missing = [c for c in required if c not in df.columns]
    if missing:
        return {'success': False, 'rows': [], 'errors': [f'Missing required columns: {missing}. Found: {list(df.columns)}'], 'warnings': [], 'total_water_charges': 0.0}

    for idx, row in df.iterrows():
        row_num = idx + 2 + header_row_used

        unit_number = str(row.get('unit_number', '')).strip()
        if not unit_number or unit_number == 'nan':
            continue

        water_charge = parse_currency(row.get('water_charge'))
        if water_charge is None:
            errors.append(f'Row {row_num}: Invalid water charge for unit {unit_number}')
            continue
        if water_charge <= 0:
            warnings.append(f'Row {row_num}: Skipping unit {unit_number} (water charge is 0)')
            continue

        total += water_charge

        tenant_name = str(row.get('tenant_name', '')).strip()
        if tenant_name == 'nan':
            tenant_name = ''

        rows.append({
            'unit_number': unit_number,
            'tenant_name': tenant_name,
            'water_charge': float(water_charge),
        })

    return {
        'success': len(errors) == 0,
        'rows': rows,
        'errors': errors,
        'warnings': warnings,
        'total_water_charges': float(total),
    }
