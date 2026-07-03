from app.services.anonymization import (
    ADDRESS_REPLACEMENT,
    APARTMENT_REPLACEMENT,
    APPEAL_REPLACEMENT,
    EMAIL_REPLACEMENT,
    PHONE_REPLACEMENT,
    PROFILE_REPLACEMENT,
    anonymize_text,
    has_unredacted_salutation_addressee,
)


def test_anonymizer_redacts_private_contacts_addresses_apartments_and_appeals() -> None:
    text = (
        "Житель Иванов Иван Иванович просит связаться: +7 999 111-22-33, "
        "email ivan@example.com, адрес ул. Ленина, д. 10, кв. 5, "
        "номер обращения АБ-12345."
    )

    result = anonymize_text(text)

    assert PHONE_REPLACEMENT in result.text
    assert EMAIL_REPLACEMENT in result.text
    assert ADDRESS_REPLACEMENT in result.text
    assert APPEAL_REPLACEMENT in result.text
    assert "+7 999 111-22-33" not in result.text
    assert "ivan@example.com" not in result.text
    assert "ул. Ленина" not in result.text
    assert "АБ-12345" not in result.text
    assert [item.detected_name for item in result.person_names] == ["Иванов Иван Иванович"]


def test_anonymizer_redacts_standalone_apartment_number() -> None:
    result = anonymize_text("Заявитель указал квартиру 42 и просит проверить начисления.")

    assert APARTMENT_REPLACEMENT in result.text
    assert "квартира 42" not in result.text


def test_anonymizer_redacts_address_with_building_and_apartment() -> None:
    text = "Собственник просит проверить домофон: улица Советская дом 10 корпус 2 квартира 45."

    result = anonymize_text(text)

    assert ADDRESS_REPLACEMENT in result.text
    assert "Советская" not in result.text
    assert "квартира 45" not in result.text


def test_anonymizer_redacts_personal_profile_links() -> None:
    result = anonymize_text("Заявитель приложил личный профиль https://vk.com/ivan.petrov.")

    assert PROFILE_REPLACEMENT in result.text
    assert "vk.com/ivan.petrov" not in result.text
    assert result.redactions[0].redaction_type == "personal_profile"


def test_anonymizer_redacts_bare_username_without_touching_email() -> None:
    result = anonymize_text("Ответ направлен пользователю @brulik0708, email отдела help@example.ru.")

    assert PROFILE_REPLACEMENT in result.text
    assert "@brulik0708" not in result.text
    assert "help@example.ru" in result.text
    assert any(item.redaction_type == "personal_profile" for item in result.redactions)


def test_anonymizer_redacts_salutation_addressee_from_screenshot_examples() -> None:
    samples = [
        "@brulik0708, Здравствуйте. Управлением транспорта г. Таганрога указано.",
        "Ni T, Здравствуйте. Решением Таганрогского суда от 01.10.2025.",
        "ВАЛЕРИЙ, Здравствуйте. В план ухода за зелеными насаждениями включено дерево.",
        "Ирина, Здравствуйте. Управлением транспорта г. Таганрога руководителю указано.",
        "  @pnmrvmrg,  Здравствуйте. Ваше обращение учтено.",
        "Aleksandr, ЗдравствуйтеСотрудники МКУ проводят проверки.",
        "[ссылка на профиль скрыта], 6Здравствуйте. Спортивная площадка проверена.",
        "Ирина,\nЗдравствуйте.В связи с аварийными работами водоснабжение понижено.",
    ]

    for sample in samples:
        result = anonymize_text(sample)

        assert result.text.startswith("Здравствуйте")
        assert "Здравствуйте" in result.text
        assert result.redactions[0].redaction_type == "salutation_addressee"
        assert result.redactions[0].replacement == ""
        assert not has_unredacted_salutation_addressee(result.text)


def test_salutation_guard_detects_unredacted_prefix() -> None:
    assert has_unredacted_salutation_addressee("@brulik0708, Здравствуйте. Текст.") is True
    assert has_unredacted_salutation_addressee("Ирина, Здравствуйте. Текст.") is True
    assert (
        has_unredacted_salutation_addressee(
            "[персональное обращение скрыто], Здравствуйте. Текст."
        )
        is False
    )
    assert has_unredacted_salutation_addressee("Здравствуйте. Текст.") is False


def test_anonymizer_keeps_official_contacts_and_addresses() -> None:
    text = (
        "Аварийная служба принимает заявки по телефону +7 495 000-00-00. "
        "Официальный email отдела: help@adm.example.ru. "
        "МФЦ расположен по адресу: ул. Центральная, д. 1. "
        "Официальный канал администрации: https://t.me/city_admin."
    )

    result = anonymize_text(text)

    assert result.text == text
    assert result.redactions == []
    assert result.review_cases == []


def test_anonymizer_marks_ambiguous_contact_for_review_after_redaction() -> None:
    result = anonymize_text("Для уточнения указан телефон 8 900 111 22 33.")

    assert PHONE_REPLACEMENT in result.text
    assert result.needs_review is True
    assert result.review_cases[0].code == "ambiguous_contact"


def test_anonymizer_sends_resident_name_to_review_without_redacting_text() -> None:
    text = "По заявлению собственника Петрова Петра Петровича начисления проверены."

    result = anonymize_text(text)

    assert result.text == text
    assert [item.detected_name for item in result.person_names] == ["Петрова Петра Петровича"]
    assert result.needs_review is True
