"""PII masking applied before any text is stored or sent to an LLM.

Deliberately conservative: it must remove contact data without mangling ordinary Japanese
(e.g. "この商品です" must survive). Names are only masked in clear name contexts
("山田様", "山田太郎と申します"), because a bare pattern would eat common nouns.
"""
import re

_RULES = [
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"), "[CARD]"),
    (re.compile(r"(?:\+81[- ]?|0)\d{1,4}[- ]\d{1,4}[- ]\d{3,4}"), "[PHONE]"),
    (re.compile(r"(?<!\d)0\d{9,10}(?!\d)"), "[PHONE]"),
    (re.compile(r"〒?\d{3}-\d{4}"), "[POSTAL]"),
    # name + honorific (山田様 / 山田 さん), or self-introduction (山田太郎と申します)
    (re.compile(r"[\u4e00-\u9fff々]{1,4}\s?(?:様|さん|殿)"), "[NAME]"),
    (re.compile(r"[\u4e00-\u9fff々]{2,4}(?=(?:と申します|といいます|と名乗))"), "[NAME]"),
    (re.compile(r"(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県).{1,20}?(?:\d+-\d+(?:-\d+)?|\d+丁目|\d+番地?)"), "[ADDRESS]"),
]


def mask(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
