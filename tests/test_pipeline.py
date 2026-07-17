"""Pipeline tests: hard filter → (fake) extraction → soft ranking & critical gating."""

from flat_hunter.matching import soft_evaluate
from flat_hunter.models import Listing, Requirement, SoftPref
from flat_hunter.pipeline import hunt


def _req():
    return Requirement(
        rooms=[1, 2, 3], price_max=5000, currency="USD",
        soft={
            "pets_allowed": SoftPref(want=True, weight=5, critical=True),
            "renovated":    SoftPref(want=True, weight=3),
            "dishwasher":   SoftPref(want=True, weight=2),
        },
    )


def _listing(code, **kw):
    base = dict(rooms=2, price=500, currency="USD")
    base.update(kw)
    return Listing(code=code, **base)


# ── soft_evaluate ────────────────────────────────────────────────────────────

def test_soft_score_weights():
    req = _req()
    feats = {"pets_allowed": True, "renovated": True, "dishwasher": False}
    r = soft_evaluate(feats, req)
    assert r.disqualified is None
    assert r.score == round((5 + 3) / (5 + 3 + 2) * 100)   # 80
    assert "dishwasher" in r.concerns
    assert set(r.fits) == {"pets_allowed", "renovated"}


def test_critical_contradiction_disqualifies():
    r = soft_evaluate({"pets_allowed": False}, _req())     # critical, contradicted
    assert r.disqualified is not None


def test_critical_unmentioned_is_flagged_not_dropped():
    r = soft_evaluate({"renovated": True}, _req())         # pets not mentioned
    assert r.disqualified is None
    assert "pets_allowed" in r.unconfirmed


# ── hunt ─────────────────────────────────────────────────────────────────────

def test_hunt_ranks_by_score_and_gates_critical():
    listings = [_listing(1), _listing(2), _listing(3)]
    feats = {
        1: {"pets_allowed": True, "renovated": True, "dishwasher": True},   # 100%
        2: {"pets_allowed": True, "renovated": False, "dishwasher": False}, # ~50%
        3: {"pets_allowed": False},                                         # disqualified
    }
    matches = hunt(listings, _req(), extract_fn=lambda l: feats[l.code])
    assert [m.listing.code for m in matches] == [1, 2]     # 3 dropped, 1 before 2
    assert matches[0].score == 100


def test_hunt_without_extractor_is_hard_only():
    listings = [_listing(1, rooms=2), _listing(2, rooms=4)]
    matches = hunt(listings, Requirement(rooms=[2]), extract_fn=None)
    assert [m.listing.code for m in matches] == [1]        # rooms gate only, no soft
    assert matches[0].soft is None


def test_hunt_sinks_scam_risky_listings():
    listings = [_listing(1), _listing(2)]
    feats = {
        1: {"pets_allowed": True, "renovated": True, "dishwasher": True, "scam_risk": "high"},
        2: {"pets_allowed": True, "renovated": True, "dishwasher": True, "scam_risk": "low"},
    }
    matches = hunt(listings, _req(), extract_fn=lambda l: feats[l.code])
    assert [m.listing.code for m in matches] == [2, 1]     # equal score, low-risk first
