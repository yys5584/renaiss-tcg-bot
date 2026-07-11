import pytest

from renaiss_bot.tools.audit_trade_volume import trades_api_url
from renaiss_bot.tools.build_master_pool import (
    canonical_identity,
    select_master_pool,
)


def _card(i: int, *, price: float, trades: int | None, name=None, set_code=None):
    return {
        "local_card_id": f"renaiss-{i:04d}",
        "category": "pokemon_tcg",
        "card_name": name or f"Card {i}",
        "set_code": set_code or f"SET{i}",
        "collector_number": str(i),
        "language": "English",
        "variation": "",
        "market_price_usd": price,
        "trade_count_90d": trades,
    }


SMALL_QUOTAS = (
    ("high_value", 2),
    ("high_volume", 2),
    ("cheap_active", 2),
    ("diversity", 2),
)


def test_quotas_are_exclusive_and_ordered():
    rows = [
        _card(1, price=1000, trades=1),
        _card(2, price=900, trades=2),
        _card(3, price=800, trades=90),
        _card(4, price=700, trades=80),
        _card(5, price=40, trades=70),
        _card(6, price=10, trades=60),
        _card(7, price=20, trades=50),
        _card(8, price=30, trades=40),
        _card(9, price=25, trades=30),
        _card(10, price=15, trades=20),
    ]
    selected = select_master_pool(rows, quotas=SMALL_QUOTAS)
    assert len(selected) == 8
    assert len({row["local_card_id"] for row in selected}) == 8
    by_quota = {q: [r["local_card_id"] for r in selected if r["quota"] == q] for q, _ in SMALL_QUOTAS}
    # price leaders take high_value, then best remaining volume takes high_volume
    assert by_quota["high_value"] == ["renaiss-0001", "renaiss-0002"]
    assert by_quota["high_volume"] == ["renaiss-0003", "renaiss-0004"]
    # cheap_active only accepts the $5-50 band with at least one trade
    assert set(by_quota["cheap_active"]) == {"renaiss-0005", "renaiss-0006"}


def test_cheap_active_requires_positive_trades_in_band():
    rows = [
        _card(1, price=1000, trades=5),
        _card(2, price=900, trades=5),
        _card(3, price=800, trades=9),
        _card(4, price=700, trades=8),
        _card(5, price=40, trades=0),  # in band but zero trades
        _card(6, price=60, trades=9),  # traded but out of band
        _card(7, price=10, trades=1),
        _card(8, price=20, trades=1),
        _card(9, price=25, trades=1),
        _card(10, price=15, trades=1),
    ]
    selected = select_master_pool(rows, quotas=SMALL_QUOTAS)
    cheap = {r["local_card_id"] for r in selected if r["quota"] == "cheap_active"}
    assert "renaiss-0005" not in cheap
    assert "renaiss-0006" not in cheap


def test_name_cap_and_identity_dedupe():
    rows = [
        _card(i, price=100 - i, trades=100 - i, name="Pikachu", set_code=f"S{i}")
        for i in range(1, 9)
    ] + [_card(20 + i, price=45 - i, trades=45 - i) for i in range(1, 9)]
    duplicate = dict(rows[0])
    duplicate["local_card_id"] = "renaiss-dupe"
    rows.append(duplicate)
    selected = select_master_pool(rows, quotas=SMALL_QUOTAS, max_per_name=3)
    names = [r["card_name"] for r in selected]
    assert names.count("Pikachu") <= 3
    ids = {r["local_card_id"] for r in selected}
    assert not {"renaiss-0001", "renaiss-dupe"} <= ids


def test_unaudited_rows_only_compete_on_price():
    rows = [
        _card(1, price=1000, trades=None),
        _card(2, price=900, trades=None),
        _card(3, price=800, trades=9),
        _card(4, price=700, trades=8),
        _card(5, price=40, trades=7),
        _card(6, price=10, trades=6),
        _card(7, price=20, trades=5),
        _card(8, price=30, trades=4),
        _card(9, price=25, trades=3),
        _card(10, price=15, trades=2),
    ]
    selected = select_master_pool(rows, quotas=SMALL_QUOTAS)
    high_value = {r["local_card_id"] for r in selected if r["quota"] == "high_value"}
    assert high_value == {"renaiss-0001", "renaiss-0002"}
    non_price = [r for r in selected if r["quota"] != "high_value"]
    assert all(isinstance(r["trade_count_90d"], int) for r in non_price)


def test_shortfall_raises():
    rows = [_card(i, price=10 + i, trades=i) for i in range(1, 5)]
    with pytest.raises(RuntimeError):
        select_master_pool(rows, quotas=SMALL_QUOTAS)


def test_already_active_rows_are_excluded_and_count_toward_caps():
    active_seed = [_card(90, price=5000, trades=None, name="Seed Grail", set_code="SEED")]
    duplicate_of_seed = dict(active_seed[0], local_card_id="renaiss-seed-dupe")
    rows = [duplicate_of_seed] + [
        _card(1, price=1000, trades=1),
        _card(2, price=900, trades=2),
        _card(3, price=800, trades=90),
        _card(4, price=700, trades=80),
        _card(5, price=40, trades=70),
        _card(6, price=10, trades=60),
        _card(7, price=20, trades=50),
        _card(8, price=30, trades=40),
        _card(9, price=25, trades=30),
        _card(10, price=15, trades=20),
    ]
    selected = select_master_pool(
        rows, quotas=SMALL_QUOTAS, already_active=active_seed
    )
    ids = {row["local_card_id"] for row in selected}
    # the seed's identity cannot re-enter through a candidate duplicate
    assert "renaiss-seed-dupe" not in ids
    assert len(selected) == 8


def test_canonical_identity_distinguishes_variation_and_language():
    base = _card(1, price=10, trades=1)
    other = dict(base, variation="Parallel")
    assert canonical_identity(base) != canonical_identity(other)
    assert canonical_identity(base) == canonical_identity(dict(base, local_card_id="x"))


def test_trades_api_url_rejects_unexpected_hrefs(monkeypatch):
    # _api_base() enforces its own host allowlist; use the default base here.
    monkeypatch.delenv("RENAISS_API_BASE_URL", raising=False)
    assert trades_api_url("/card/pokemon/base/1-pikachu-psa-10-aa") == (
        "https://api.renaissos.com/v1/cards/pokemon/base/1-pikachu-psa-10-aa/trades"
    )
    assert trades_api_url("/sets/pokemon/base") is None
    assert trades_api_url("/card/pokemon/base/1?x=1") is None
    assert trades_api_url("") is None
