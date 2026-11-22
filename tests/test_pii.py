"""Masking and log-hygiene tests (Japanese false positives included)."""
import common
from pii import mask


def test_contact_data_is_masked():
    assert mask("ご連絡先は090-1234-5678です") == "ご連絡先は[PHONE]です"
    assert mask("0120-123-456 まで") == "[PHONE] まで"
    assert mask("09012345678") == "[PHONE]"
    assert mask("mail: a.b@example.co.jp") == "mail: [EMAIL]"
    assert mask("〒150-0001 東京都渋谷区1-2-3") == "[POSTAL] [ADDRESS]"
    assert mask("4111 1111 1111 1111") == "[CARD]"


def test_names_masked_only_in_name_contexts():
    assert mask("山田様、ありがとうございます。") == "[NAME]、ありがとうございます。"
    assert mask("山田太郎と申します。") == "[NAME]と申します。"
    # ordinary prose must not be mangled
    assert mask("この商品です。おすすめです。") == "この商品です。おすすめです。"
    assert mask("在庫があります") == "在庫があります"


def test_log_tag_does_not_contain_member_id():
    mid = "a" * 32
    tag = common.member_log_tag(mid)
    assert mid not in tag and len(tag) == 10
    assert tag == common.member_log_tag(mid)  # stable, so logs stay correlatable
