"""Excel parser for tenant/unit bulk import."""
import pandas as pd
from decimal import Decimal
from typing import Optional
import re


def parse_currency(value) -> Optional[Decimal]:
    """Parse currency string like '15,000' or 'KES 15,000' to Decimal."""
    if pd.isna(value):
        return Decimal('0')
    s = str(value).strip()
    if not s:
        return Decimal('0')
    # Remove currency symbols, commas, spaces
    s = re.sub(r'[KES,\s]', '', s, flags=re.IGNORECASE)
    if not s:
        return Decimal('0')
    try:
        return Decimal(s)
    except Exception:
        return None


def normalize_column_name(col: str) -> str:
    """Normalize column names to standard keys."""
    # Clean up: lowercase, strip whitespace and newlines
    col = col.lower().strip().replace('\n', ' ').replace('\r', ' ')
    # Collapse multiple spaces
    col = re.sub(r'\s+', ' ', col)

    mappings = {
        'unit no': 'unit_number',
        'unit no.': 'unit_number',
        'unit number': 'unit_number',
        'unit': 'unit_number',
        'tenant name': 'tenant_name',
        'tenant': 'tenant_name',
        'name': 'tenant_name',
        'apartment size': 'apartment_size',
        'size': 'apartment_size',
        'monthly rent': 'monthly_rent',
        'monthly rent (kes)': 'monthly_rent',
        'montly rent': 'monthly_rent',
        'montly rent (kes)': 'monthly_rent',
        'rent': 'monthly_rent',
        'service charge': 'service_charge',
        'service charge (kes)': 'service_charge',
        'contact': 'contact',
        'phone': 'contact',
        'pending rent': 'pending_rent',
        'pending rent (kes)': 'pending_rent',
        'arrears': 'pending_rent',
        'balance': 'pending_rent',
        'status': 'status',
    }
    return mappings.get(col, col.replace(' ', '_'))


def normalize_phone(contact: str) -> str:
    """Normalize phone number - add leading 0 if 9-digit Kenyan number."""
    if not contact:
        return contact
    # Remove any spaces, dashes, or dots
    cleaned = re.sub(r'[\s\-\.]', '', contact)
    # If 9 digits starting with 7 or 1, add leading 0
    if len(cleaned) == 9 and cleaned[0] in '17':
        cleaned = '0' + cleaned
    return cleaned


def parse_tenant_excel(file_path: str) -> dict:
    """
    Parse Excel file with tenant/unit data.

    Returns:
        {
            'success': bool,
            'rows': list of dicts with normalized keys,
            'errors': list of error messages,
            'warnings': list of warning messages,
            'total_arrears': float
        }
    """
    errors = []
    warnings = []
    rows = []
    total_arrears = Decimal('0')

    # Auto-detect header row (some files have title rows above headers)
    df = None
    header_row_used = 0
    for header_row in [0, 1, 2]:
        try:
            candidate = pd.read_excel(file_path, header=header_row)
            cols = [normalize_column_name(str(c)) for c in candidate.columns]
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
            return {'success': False, 'rows': [], 'errors': [f'Failed to read Excel file: {e}'], 'warnings': [], 'total_arrears': 0.0}

    if df.empty:
        return {'success': False, 'rows': [], 'errors': ['Excel file is empty'], 'warnings': [], 'total_arrears': 0.0}

    if header_row_used > 0:
        warnings.append(f'Header row detected at row {header_row_used + 1} (skipped {header_row_used} title row(s))')

    # Normalize column names
    df.columns = [normalize_column_name(str(c)) for c in df.columns]

    # Check required columns
    required = ['unit_number', 'monthly_rent']
    missing = [c for c in required if c not in df.columns]
    if missing:
        return {'success': False, 'rows': [], 'errors': [f'Missing required columns: {missing}. Found: {list(df.columns)}'], 'warnings': [], 'total_arrears': 0.0}

    for idx, row in df.iterrows():
        row_num = idx + 2 + header_row_used  # Excel row number

        unit_number = str(row.get('unit_number', '')).strip()
        if not unit_number or unit_number == 'nan':
            continue

        tenant_name = str(row.get('tenant_name', '')).strip()
        if tenant_name == 'nan':
            tenant_name = ''

        # Handle OFFICE row - all fields are "OFFICE"
        if tenant_name.upper() == 'OFFICE':
            rows.append({
                'unit_number': unit_number,
                'tenant_name': '',
                'apartment_size': 'Office',
                'monthly_rent': 0.0,
                'service_charge': 0.0,
                'contact': '',
                'pending_rent': 0.0,
                'status': 'office',
            })
            warnings.append(f'Row {row_num}: Unit {unit_number} marked as OFFICE (rent=0)')
            continue

        # Handle Vacant units
        if tenant_name.lower() == 'vacant':
            tenant_name = ''

        monthly_rent = parse_currency(row.get('monthly_rent'))
        if monthly_rent is None:
            errors.append(f'Row {row_num}: Invalid monthly rent for unit {unit_number}')
            continue

        service_charge = parse_currency(row.get('service_charge', 0)) or Decimal('0')
        pending_rent = parse_currency(row.get('pending_rent', 0)) or Decimal('0')
        total_arrears += pending_rent

        contact = str(row.get('contact', '')).strip()
        if contact == 'nan':
            contact = ''
        # Remove .0 from phone numbers read as floats
        if contact.endswith('.0'):
            contact = contact[:-2]
        contact = normalize_phone(contact)

        apartment_size = str(row.get('apartment_size', '')).strip()
        if apartment_size == 'nan':
            apartment_size = ''

        status = str(row.get('status', 'active')).strip().lower()
        if status == 'nan' or not status:
            status = 'active'
        elif status in ('paid', 'pending'):
            status = 'active'

        rows.append({
            'unit_number': unit_number,
            'tenant_name': tenant_name,
            'apartment_size': apartment_size,
            'monthly_rent': float(monthly_rent),
            'service_charge': float(service_charge),
            'contact': contact,
            'pending_rent': float(pending_rent),
            'status': status,
        })

    return {
        'success': len(errors) == 0,
        'rows': rows,
        'errors': errors,
        'warnings': warnings,
        'total_arrears': float(total_arrears)
    }
