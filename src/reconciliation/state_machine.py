"""
State Machine for SMS Claim Management

Handles:
- Duplicate detection
- State transitions
- Persistent storage (JSON file)
- Status tracking
"""

import json
import os
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from decimal import Decimal
from ..parsers.sms_parser import SMSClaim, ClaimStatus


class ClaimStateMachine:
    """Manages state transitions for SMS claims with file persistence."""
    
    def __init__(self, state_file: str = 'data/state/claims.json'):
        """
        Initialize state machine.
        
        Args:
            state_file: Path to JSON file for persistent storage
        """
        self.state_file = state_file
        self.claims: Dict[str, SMSClaim] = {}  # reference -> claim
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        
        # Load existing state
        self.load_state()
    
    def _claim_to_dict(self, claim: SMSClaim) -> dict:
        """Convert SMSClaim to dictionary for JSON serialization."""
        return {
            'reference': claim.reference,
            'amount': str(claim.amount) if claim.amount is not None else None,
            'timestamp': claim.timestamp.isoformat() if claim.timestamp else None,
            'received_at': claim.received_at.isoformat(),
            'sender_hint': claim.sender_hint,
            'status': claim.status.value,
            'format_detected': claim.format_detected,
            'raw_text': claim.raw_text,
            'parse_warnings': claim.parse_warnings,
            'bank_match_id': claim.bank_match_id,
            'bank_amount': str(claim.bank_amount) if claim.bank_amount is not None else None,
        }
    
    def _dict_to_claim(self, data: dict) -> SMSClaim:
        """Convert dictionary to SMSClaim."""
        return SMSClaim(
            reference=data['reference'],
            amount=Decimal(data['amount']) if data.get('amount') else None,
            timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else None,
            received_at=datetime.fromisoformat(data['received_at']),
            sender_hint=data.get('sender_hint'),
            status=ClaimStatus(data['status']),
            format_detected=data.get('format_detected', 'unknown'),
            raw_text=data.get('raw_text', ''),
            parse_warnings=data.get('parse_warnings', []),
            bank_match_id=data.get('bank_match_id'),
            bank_amount=Decimal(data['bank_amount']) if data.get('bank_amount') else None,
        )
    
    def load_state(self):
        """Load state from JSON file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.claims = {
                        ref: self._dict_to_claim(claim_data)
                        for ref, claim_data in data.items()
                    }
            except Exception as e:
                print(f"Warning: Could not load state from {self.state_file}: {e}")
                self.claims = {}
        else:
            self.claims = {}
    
    def save_state(self):
        """Save state to JSON file."""
        try:
            data = {
                ref: self._claim_to_dict(claim)
                for ref, claim in self.claims.items()
            }
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save state to {self.state_file}: {e}")
    
    def add_claim(self, claim: SMSClaim) -> Dict:
        """
        Add a new claim, checking for duplicates.
        
        Returns:
            {
                'success': bool,
                'status': ClaimStatus,
                'is_duplicate': bool,
                'existing_claim': Optional[SMSClaim],
                'message': str,
                'claim': SMSClaim  # The claim (new or existing)
            }
        """
        # Check for duplicate reference
        if claim.reference in self.claims:
            existing = self.claims[claim.reference]
            
            # Check if amounts match (if both have amounts)
            if claim.amount is not None and existing.amount is not None:
                if abs(claim.amount - existing.amount) < Decimal('0.01'):
                    # Same reference + same amount = duplicate
                    return {
                        'success': False,
                        'status': ClaimStatus.DUPLICATE,
                        'is_duplicate': True,
                        'existing_claim': existing,
                        'message': f'Duplicate reference code: {claim.reference} (same amount: {existing.amount})',
                        'claim': existing
                    }
                else:
                    # Same reference + different amount = CRITICAL ERROR
                    return {
                        'success': False,
                        'status': ClaimStatus.AMOUNT_MISMATCH,
                        'is_duplicate': False,
                        'existing_claim': existing,
                        'message': f'Reference {claim.reference} already exists with different amount. Existing: {existing.amount}, New: {claim.amount}',
                        'claim': existing
                    }
            else:
                # Same reference, one or both missing amount = potential duplicate
                return {
                    'success': False,
                    'status': ClaimStatus.DUPLICATE,
                    'is_duplicate': True,
                    'existing_claim': existing,
                    'message': f'Duplicate reference code: {claim.reference}',
                    'claim': existing
                }
        
        # New claim - add it
        claim.status = ClaimStatus.CLAIMED
        self.claims[claim.reference] = claim
        self.save_state()
        
        return {
            'success': True,
            'status': ClaimStatus.CLAIMED,
            'is_duplicate': False,
            'existing_claim': None,
            'message': f'New claim added: {claim.reference}',
            'claim': claim
        }
    
    def get_claim(self, reference: str) -> Optional[SMSClaim]:
        """Get claim by reference code."""
        return self.claims.get(reference)
    
    def update_status(self, reference: str, new_status: ClaimStatus, **kwargs) -> bool:
        """Update claim status."""
        if reference not in self.claims:
            return False
        
        claim = self.claims[reference]
        claim.status = new_status
        
        # Update additional fields if provided
        if 'bank_match_id' in kwargs:
            claim.bank_match_id = kwargs['bank_match_id']
        if 'bank_amount' in kwargs:
            claim.bank_amount = kwargs['bank_amount']
        
        self.save_state()
        return True
    
    def get_unconfirmed_claims(self, days: int = 7) -> List[SMSClaim]:
        """Get claims that haven't been confirmed after N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            claim for claim in self.claims.values()
            if claim.status == ClaimStatus.CLAIMED
            and claim.received_at < cutoff
        ]
    
    def get_all_claims(self) -> List[SMSClaim]:
        """Get all claims."""
        return list(self.claims.values())
    
    def get_claims_by_status(self, status: ClaimStatus) -> List[SMSClaim]:
        """Get claims by status."""
        return [claim for claim in self.claims.values() if claim.status == status]
    
    def get_statistics(self) -> Dict:
        """Get statistics about all claims."""
        total = len(self.claims)
        by_status = {}
        for status in ClaimStatus:
            by_status[status.value] = len(self.get_claims_by_status(status))
        
        return {
            'total_claims': total,
            'by_status': by_status
        }
