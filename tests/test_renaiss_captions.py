"""Collection-only prices must not masquerade as verified Renaiss FMV."""

from __future__ import annotations

from renaiss_bot.services.captions import _price_line
from renaiss_bot.services.models import RenaissPrice


def test_candidate_pack_price_is_explicitly_unverified():
    line = _price_line(
        RenaissPrice(
            status="candidate",
            source="renaiss-index-api",
            fmv_usd=95,
        )
    )

    assert "Candidate reference value" in line
    assert "unverified" in line
    assert "Renaiss reference FMV" not in line
