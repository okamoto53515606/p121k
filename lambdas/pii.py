"""PII masking applied before any text is stored or sent to an LLM."""
import re

_RULES = [
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"), "[CARD]"),
    (re.compile(r"(?:\+81[- ]?|0)\d{1,4}[- ]\d{1,4}[- ]\d{3,4}"), "[PHONE]"),
    (re.compile(r"(?<!\d)0\d{9,10}(?!\d)"), "[PHONE]"),
    (re.compile(r"〒?\d{3}-\d{4}"), "[POSTAL]"),
    (re.compile(r"[\u4e00-\u9fff々ヶ]{2,4}(?=(?:です|と申します|と言います|といいます))"), "[NAME]"),
    (re.compile(r"(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県).{1,20}?(?:\d+-\d+(?:-\d+)?|\d+丁目|\d+番地?)"), "[ADDRESS]"),
]


def mask(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
