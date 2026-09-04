from taxhance_pii.domain import PiiCategory
from taxhance_pii.redaction.prompt import (
    CATEGORY_MAP,
    PROMPT_VERSION,
    RESPONSE_JSON_SCHEMA,
    build_messages,
)


def test_category_map_targets_domain_enum() -> None:
    assert CATEGORY_MAP == {
        "client_name": PiiCategory.PERSON_NAME,
        "client_tin": PiiCategory.SSN,
        "address_line": PiiCategory.STREET_ADDRESS,
        "private_id": PiiCategory.OTHER_PRIVATE_ID,
        "email": PiiCategory.EMAIL,
        "phone": PiiCategory.PHONE,
        "dob": PiiCategory.DATE_OF_BIRTH,
    }


def test_schema_enum_matches_map_keys() -> None:
    enum = RESPONSE_JSON_SCHEMA["json_schema"]["schema"]["properties"]["items"]["items"][
        "properties"
    ]["category"]["enum"]
    assert sorted(enum) == sorted(CATEGORY_MAP)


def test_messages_carry_policy_and_grid() -> None:
    messages = build_messages("THE GRID")
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "THE GRID" in user
    assert "payer" in user.lower() and "never" in user.lower()
    assert "ZIP" in user


def test_version() -> None:
    assert PROMPT_VERSION == "tax-pii-v8"
