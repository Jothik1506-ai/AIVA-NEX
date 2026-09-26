"""Detection tests. Only published test numbers / synthetic values are used:
999941057058 (UIDAI sandbox Aadhaar), 234567890124 (synthetic, Verhoeff check
digit computed), card-network test numbers, and a fictional PAN."""

import pytest

from pii_checks import (
    aadhaar_valid,
    card_valid,
    detect_pii,
    find_validated_pii_types,
    luhn_valid,
    pan_valid,
    verhoeff_check_digit,
    verhoeff_valid,
)


def test_verhoeff_known_vectors():
    assert verhoeff_valid("2363")
    assert not verhoeff_valid("2364")
    assert verhoeff_check_digit("236") == "3"
    assert verhoeff_valid("999941057058")
    assert not verhoeff_valid("999941057059")


@pytest.mark.parametrize("value", ["999941057058", "9999 4105 7058", "9999-4105-7058", "234567890124"])
def test_aadhaar_valid(value):
    assert aadhaar_valid(value)


def test_aadhaar_invalid():
    assert not aadhaar_valid("234567890123")  # wrong check digit
    base = "12345678901"
    assert not aadhaar_valid(base + verhoeff_check_digit(base))  # first digit 1
    assert not aadhaar_valid("99994105705")  # 11 digits


@pytest.mark.parametrize(
    "value",
    ["4111111111111111", "4111 1111 1111 1111", "5555-5555-5555-4444", "378282246310005", "4222222222222"],
)
def test_card_valid(value):
    assert card_valid(value)


def test_card_invalid():
    assert not card_valid("4111111111111112")
    assert not card_valid("411111111111")
    assert luhn_valid("79927398713")
    assert not luhn_valid("79927398710")


def test_pan():
    assert pan_valid("ABCPE1234F")
    assert pan_valid("abcpe1234f")
    assert not pan_valid("ABCXE1234F")
    assert not pan_valid("ABCP1234F")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("id 9999 4105 7058", [("AADHAAR", True)]),
        ("order 234567890123", [("AADHAAR", False)]),
        ("ref 4111111111111112", [("CARD", False)]),
        ("pay 4111 1111 1111 1111 now", [("CARD", True)]),
        ("call 9876543210", [("PHONE", True)]),
        ("call +91-98765 43210", [("PHONE", True)]),
        ("mail priya@example.com", [("EMAIL", True)]),
        ("PAN ABCPE1234F", [("PAN", True)]),
        ("code ABCXE1234F", [("PAN", False)]),
        ("order 123456789012 costs 1299 on 2026-09-26", []),
        ("landline 0401234567", []),
        ("tokens PHONE_1 EMAIL_2 ID_NUMBER_3", []),
    ],
)
def test_detect_pii_matches_extension_logic(text, expected):
    # Same vectors as extension/tests/pii-checks.test.js
    assert [(m["type"], m["validated"]) for m in detect_pii(text)] == expected


def test_card_not_split_into_aadhaar():
    m = detect_pii("4111 1111 1111 1111")
    assert [x["type"] for x in m] == ["CARD"]


def test_find_validated_types_walks_nested_payload():
    payload = {"inputs": [{"sanitizedValue": "PHONE_1"}, {"sanitizedValue": "x 4111111111111111"}], "n": 999941057058}
    assert find_validated_pii_types(payload) == ["aadhaar", "card"]


def test_find_validated_types_ignores_candidates():
    assert find_validated_pii_types({"t": "order 234567890123, ref 4111111111111112"}) == []
