#!/usr/bin/env python3
"""Quick test: run Vision OCR on an image and print raw text."""
import sys
from backend.ocr_parser import ocr_image

if len(sys.argv) < 2:
    print("用法: python3 ocr_test.py 截图.png")
    sys.exit(1)

with open(sys.argv[1], "rb") as f:
    text = ocr_image(f.read())

print(text if text else "(无识别结果)")
print(f"\n共 {len(text.split(chr(10)))} 行")
