"""Price-vs-comparables (pure, no model)."""

from flat_hunter.ai.pricing import comparables, price_verdict
from flat_hunter.models import Listing


def _l(code, price, rooms=2, area=50.0, town="Минск", district="Центральный"):
    return Listing(code=code, price=price, currency="USD", rooms=rooms,
                   area_total=area, town=town, district=district)


def _corpus():
    return [_l(1, 500), _l(2, 520), _l(3, 480), _l(4, 510),
            _l(5, 900, rooms=3),                 # different rooms → not comparable
            _l(6, 400, district="Другой")]       # different district → not comparable


def test_comparables_filters_by_structure():
    target = _l(99, 505)
    comps = comparables(target, _corpus())
    codes = {c.code for c in comps}
    assert codes == {1, 2, 3, 4}                 # same rooms/district/area band only


def test_below_market_verdict():
    target = _l(99, 400)                          # well under the ~505 median
    info = price_verdict(target, _corpus())
    assert info.n_comparables == 4
    assert info.verdict == "below market"
    assert info.delta_pct < 0


def test_above_market_verdict():
    info = price_verdict(_l(99, 700), _corpus())
    assert info.verdict == "above market"


def test_not_enough_comparables_is_na():
    info = price_verdict(_l(99, 500), [_l(1, 500)])   # only 1 comp
    assert info.verdict == "n/a"
    assert "not enough" in info.line().lower()
