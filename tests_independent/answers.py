"""Helpers for black-box checking of free-form agent answers."""

from __future__ import annotations

import re
import unittest

_SPACES = "\u00a0\u202f\u2009\u2007\u2008\u200a"
_APOSTROPHES = "\u2018\u2019\u02bb\u02bc`\u00b4"

_NUMBER = re.compile(
    r"(?:\d{1,3}(?:[ " + _SPACES + r"]\d{3})+(?:[.,]\d+)?)"
    r"|(?:\d+(?:[.,]\d+)?)"
)

_MULTIPLIERS = (
    (re.compile(r"(?i)\b(?:трлн|trillion|trln)\b"), 1e12),
    (re.compile(r"(?i)\b(?:млрд|milliard|billion|mlrd)\b"), 1e9),
    (re.compile(r"(?i)\b(?:млн|million|mln)\b"), 1e6),
    (re.compile(r"(?i)\b(?:тыс(?:\.|яч)?|thousand|ming)\b"), 1e3),
)

_NO_DATA = (
    "нет данных",
    "не найдено",
    "не найден",
    "не найдена",
    "нет в реестре",
    "в реестре нет",
    "реестре нет",
    "не содержится",
    "не встречается",
    "отсутствует",
    "не удалось найти",
    "yo'q",
    "topilmadi",
    "mavjud emas",
    "ma'lumot yo'q",
)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n")
    for ch in _SPACES:
        text = text.replace(ch, " ")
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    for ch in '«»"“”„':
        text = text.replace(ch, "")
    return re.sub(r"[ \t]+", " ", text).strip()


def number_candidates(text: str) -> list[float]:
    """All plausible numeric values in ``text`` (including тыс/млн/млрд/трлн)."""
    text = normalize(text)
    values: list[float] = []
    for match in _NUMBER.finditer(text):
        token = match.group(0)
        compact = token.replace(" ", "")
        variants: set[float] = set()
        if "," in compact and "." in compact:
            # The last separator is decimal, the other one groups thousands.
            last = max(compact.rfind(","), compact.rfind("."))
            integer = re.sub(r"[.,]", "", compact[:last])
            fraction = re.sub(r"[.,]", "", compact[last + 1 :])
            variants.add(float(f"{integer}.{fraction or '0'}"))
        else:
            # Accept both "1,234.5"/"1.234,5" and plain decimal notation.
            try:
                variants.add(float(compact.replace(",", ".")))
            except ValueError:
                pass
            try:
                variants.add(float(re.sub(r"[.,]", "", compact)))
            except ValueError:
                pass
        multiplier = 1.0
        tail = text[match.end() : match.end() + 16]
        for pattern, factor in _MULTIPLIERS:
            if pattern.search(tail):
                multiplier = factor
                break
        values.extend(v * multiplier for v in variants)
    return values


def assert_number(
    case: unittest.TestCase,
    text: str,
    expected: float,
    *,
    rel_tol: float = 1e-9,
    abs_tol: float = 0.5,
    context: str = "",
) -> None:
    candidates = number_candidates(text)
    for candidate in candidates:
        if abs(candidate - float(expected)) <= max(
            abs(float(expected)) * rel_tol, abs_tol
        ):
            return
    case.fail(
        f"{context or 'number'} {expected!r} not found in answer.\n"
        f"answer: {text!r}\n"
        f"numbers parsed: {candidates!r}"
    )


def assert_no_data(case: unittest.TestCase, text: str) -> None:
    lowered = normalize(text).lower()
    if any(phrase in lowered for phrase in _NO_DATA):
        return
    case.fail(f"answer does not state that the data is missing: {text!r}")
