"""Redact obvious personal data before a prompt leaves the network boundary."""
import re

PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<EMAIL>"),
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "<TW_ID>"),                     # Taiwan national ID
    (re.compile(r"\b09\d{2}-?\d{3}-?\d{3}\b"), "<PHONE>"),             # Taiwan mobile
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "<CARD>"),
]


def redact(text: str) -> tuple[str, int]:
    n = 0
    for rx, repl in PATTERNS:
        text, k = rx.subn(repl, text)
        n += k
    return text, n
