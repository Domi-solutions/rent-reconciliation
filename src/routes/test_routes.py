"""
Test routes for parser testing and CRUD development testing.
Parser endpoints return JSON and do not write to the database.
CRUD endpoints commit to DB for development testing only; not for production.
"""
import os
import tempfile
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from src.parsers.router import parse_input, detect_input_type, InputType

test_bp = Blueprint('test', __name__, url_prefix='/test')


@test_bp.route('/parse', methods=['POST'])
def test_parse():
    """
    Universal test endpoint - auto-detects and parses any input.
    For text (SMS): POST with Content-Type: text/plain or JSON {"text": "..."}
    For files: POST with multipart/form-data
    """
    if 'file' in request.files:
        file = request.files['file']
        if file.filename:
            filename = secure_filename(file.filename)
            temp_dir = tempfile.mkdtemp()
            temp_path = os.path.join(temp_dir, filename)
            file.save(temp_path)
            try:
                result = parse_input(temp_path, filename=filename)
                return jsonify(result.to_dict())
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                os.rmdir(temp_dir)

    text = None
    if request.is_json:
        text = request.json.get('text')
    elif request.content_type == 'text/plain':
        text = request.get_data(as_text=True)
    else:
        text = request.form.get('text')

    if text:
        result = parse_input(text)
        return jsonify(result.to_dict())

    return jsonify({
        'success': False,
        'error': 'No input provided. Send file or text.'
    }), 400


@test_bp.route('/parse/sms', methods=['POST'])
def test_parse_sms():
    """Test SMS parser specifically."""
    text = request.json.get('text') if request.is_json else request.form.get('text')
    if not text:
        return jsonify({'success': False, 'error': 'No text provided'}), 400
    result = parse_input(text, input_type=InputType.MPESA_SMS)
    return jsonify(result.to_dict())


@test_bp.route('/parse/excel', methods=['POST'])
def test_parse_excel():
    """Test Excel parser specifically."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, filename)
    file.save(temp_path)
    try:
        result = parse_input(temp_path, input_type=InputType.TENANT_EXCEL)
        return jsonify(result.to_dict())
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        os.rmdir(temp_dir)


@test_bp.route('/parse/pdf', methods=['POST'])
def test_parse_pdf():
    """Test PDF parser specifically."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, filename)
    file.save(temp_path)
    try:
        result = parse_input(temp_path, input_type=InputType.BANK_STATEMENT_PDF)
        return jsonify(result.to_dict())
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        os.rmdir(temp_dir)


@test_bp.route('/detect', methods=['POST'])
def test_detect():
    """Test input type detection without parsing."""
    if 'file' in request.files:
        file = request.files['file']
        detected = detect_input_type('', filename=file.filename)
        return jsonify({
            'filename': file.filename,
            'detected_type': detected.value
        })
    text = request.json.get('text') if request.is_json else request.form.get('text')
    if text:
        detected = detect_input_type(text)
        return jsonify({
            'text_preview': text[:100] + '...' if len(text) > 100 else text,
            'detected_type': detected.value
        })
    return jsonify({'error': 'No input provided'}), 400


# --- CRUD test endpoints (commit to DB for development testing only) ---


@test_bp.route('/crud/property', methods=['POST'])
def test_create_property():
    """
    Test property creation.
    Commits to DB for development testing. Not for production.
    """
    from src.database.db import get_connection, generate_id

    data = request.json or {}
    name = data.get('name', 'Test Property')
    address = data.get('address', '')

    with get_connection() as conn:
        property_id = generate_id('PROP')
        conn.execute(
            "INSERT INTO properties (id, name, address) VALUES (?, ?, ?)",
            (property_id, name, address)
        )
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

    return jsonify({
        'success': True,
        'property': dict(prop) if prop else None,
        'note': 'Transaction committed - property exists in DB'
    })


@test_bp.route('/crud/unit', methods=['POST'])
def test_create_unit():
    """
    Test unit creation.
    Commits to DB for development testing. Not for production.
    """
    from src.database.db import get_connection

    data = request.json or {}
    property_id = data.get('property_id')
    unit_number = data.get('unit_number', 'TEST-1')
    monthly_rent = data.get('monthly_rent', 15000)

    if not property_id:
        return jsonify({'success': False, 'error': 'property_id required'}), 400

    with get_connection() as conn:
        unit_id = f"{property_id}-{unit_number}"
        try:
            conn.execute(
                "INSERT INTO units (id, property_id, unit_number, monthly_rent, status_changed_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (unit_id, property_id, unit_number, monthly_rent)
            )
            return jsonify({'success': True, 'unit_id': unit_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400


@test_bp.route('/crud/tenant', methods=['POST'])
def test_create_tenant():
    """
    Test tenant creation.
    Commits to DB for development testing. Not for production.
    """
    from src.database.db import get_connection, generate_id

    data = request.json or {}
    property_id = data.get('property_id')
    unit_id = data.get('unit_id')
    name = data.get('name', 'Test Tenant')
    phone = data.get('phone', '')

    if not property_id or not unit_id:
        return jsonify({'success': False, 'error': 'property_id and unit_id required'}), 400

    with get_connection() as conn:
        tenant_id = generate_id('TENANT')
        try:
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, status) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, property_id, unit_id, name, phone, 'active')
            )
            return jsonify({'success': True, 'tenant_id': tenant_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400


@test_bp.route('/crud/charge', methods=['POST'])
def test_create_charge():
    """
    Test rent charge creation.
    Commits to DB for development testing. Not for production.
    """
    from src.database.db import get_connection, generate_id

    data = request.json or {}
    property_id = data.get('property_id')
    unit_id = data.get('unit_id')
    period = data.get('period', '2026-02')
    amount = data.get('amount', 15000)

    if not property_id or not unit_id:
        return jsonify({'success': False, 'error': 'property_id and unit_id required'}), 400

    with get_connection() as conn:
        charge_id = generate_id('CHG')
        try:
            charge_type = data.get('charge_type', 'rent')
            conn.execute(
                "INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) VALUES (?, ?, ?, ?, ?, ?)",
                (charge_id, property_id, unit_id, period, charge_type, amount)
            )
            return jsonify({'success': True, 'charge_id': charge_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400


@test_bp.route('/crud/balance/<unit_id>', methods=['GET'])
def test_get_balance(unit_id):
    """Test balance calculation for a unit (read-only)."""
    from src.database.db import get_connection

    with get_connection() as conn:
        result = conn.execute("""
            SELECT
                u.unit_number,
                u.monthly_rent,
                COALESCE(SUM(rc.amount), 0) as total_charged,
                COALESCE(SUM(p.amount), 0) as total_paid,
                COALESCE(SUM(rc.amount), 0) - COALESCE(SUM(p.amount), 0) as balance
            FROM units u
            LEFT JOIN rent_charges rc ON rc.unit_id = u.id
            LEFT JOIN payments p ON p.unit_id = u.id
            WHERE u.id = ?
            GROUP BY u.id
        """, (unit_id,)).fetchone()

        if not result:
            return jsonify({'success': False, 'error': 'Unit not found'}), 404

        return jsonify({
            'success': True,
            'unit_number': result['unit_number'],
            'monthly_rent': result['monthly_rent'],
            'total_charged': result['total_charged'],
            'total_paid': result['total_paid'],
            'balance': result['balance']
        })
