"""Relist detection: embeddings + structural gate (vectors supplied, no model)."""

from flat_hunter.ai.dedup import cosine, find_relist
from flat_hunter.models import Listing


def _l(code, price, rooms=2, area=50.0, district="Центральный"):
    return Listing(code=code, price=price, currency="USD", rooms=rooms,
                   area_total=area, district=district)


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert abs(cosine([1, 1], [1, 1]) - 1.0) < 1e-9


def test_finds_relist_with_similar_text_and_structure():
    candidate = _l(2, 460)                        # same flat, reposted $40 cheaper
    corpus = [(_l(1, 500), [1.0, 0.0, 0.0])]
    info = find_relist(candidate, [0.99, 0.01, 0.0], corpus)
    assert info is not None
    assert info.code == 1
    assert info.price_change == -40               # 460 - 500
    assert "↓40" in info.line()


def test_structural_gate_blocks_different_rooms():
    candidate = _l(2, 460, rooms=3)               # different room count
    corpus = [(_l(1, 500, rooms=2), [1.0, 0.0, 0.0])]
    assert find_relist(candidate, [1.0, 0.0, 0.0], corpus) is None   # identical text, but gated out


def test_low_similarity_is_not_a_relist():
    candidate = _l(2, 460)
    corpus = [(_l(1, 500), [0.0, 1.0, 0.0])]      # orthogonal text
    assert find_relist(candidate, [1.0, 0.0, 0.0], corpus) is None
