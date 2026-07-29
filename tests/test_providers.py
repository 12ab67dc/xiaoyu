import pytest

from quant_app.providers import normalize_cn_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("510300", "sh510300"),
        ("600519", "sh600519"),
        ("159915", "sz159915"),
        ("000001.SZ", "sz000001"),
        ("SH510300", "sh510300"),
    ],
)
def test_normalize_cn_symbol(raw: str, expected: str) -> None:
    assert normalize_cn_symbol(raw) == expected


def test_normalize_cn_symbol_rejects_invalid_code() -> None:
    with pytest.raises(ValueError):
        normalize_cn_symbol("QQQ")
