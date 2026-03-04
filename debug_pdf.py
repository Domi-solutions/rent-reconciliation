#!/usr/bin/env python3

"""Debug script to inspect PDF text extraction."""

import pdfplumber
import sys

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "data/input/ACSTMT-VIEW (2)-1.pdf"

print("=" * 80)
print("DEBUGGING PDF TEXT EXTRACTION")
print("=" * 80)
print()

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total pages: {len(pdf.pages)}\n")
    
    # Show first 100 lines from first page
    first_page = pdf.pages[0]
    text = first_page.extract_text()
    
    if text:
        lines = text.split('\n')
        print(f"First page has {len(lines)} lines\n")
        print("First 50 lines:")
        print("-" * 80)
        for i, line in enumerate(lines[:50], 1):
            print(f"{i:3}: {line}")
        
        print("\n" + "=" * 80)
        print("Looking for date patterns...")
        print("=" * 80)
        
        import re
        date_pattern1 = re.compile(r'^\d{2}-[A-Z]{3}-\d{2,4}')
        date_pattern2 = re.compile(r'\d{2}-[A-Z]{3}-\d{2,4}')
        
        matches_start = []
        matches_anywhere = []
        
        for i, line in enumerate(lines[:100]):
            if date_pattern1.match(line.strip()):
                matches_start.append((i+1, line.strip()))
            if date_pattern2.search(line):
                matches_anywhere.append((i+1, line.strip()))
        
        print(f"\nLines starting with date pattern (DD-MMM-YYYY): {len(matches_start)}")
        for line_num, line in matches_start[:10]:
            print(f"  Line {line_num}: {line}")
        
        print(f"\nLines containing date pattern anywhere: {len(matches_anywhere)}")
        for line_num, line in matches_anywhere[:10]:
            print(f"  Line {line_num}: {line}")
        
        # Check for transaction-like patterns
        print("\n" + "=" * 80)
        print("Looking for transaction indicators...")
        print("=" * 80)
        
        paybill_pattern = re.compile(r'Paybill', re.IGNORECASE)
        code_pattern = re.compile(r'\s(\d{3})$')
        
        paybill_lines = []
        code_lines = []
        
        for i, line in enumerate(lines[:200]):
            if paybill_pattern.search(line):
                paybill_lines.append((i+1, line.strip()))
            if code_pattern.search(line):
                code_lines.append((i+1, line.strip()))
        
        print(f"\nLines containing 'Paybill': {len(paybill_lines)}")
        for line_num, line in paybill_lines[:5]:
            print(f"  Line {line_num}: {line}")
        
        print(f"\nLines ending with 3-digit code: {len(code_lines)}")
        for line_num, line in code_lines[:10]:
            print(f"  Line {line_num}: {line}")
