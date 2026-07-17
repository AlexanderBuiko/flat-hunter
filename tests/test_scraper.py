"""Parser tests against a saved realt.by objects fixture (offline, no network)."""

import json
import pathlib

from flat_hunter.scraper import objects_to_listings, extract_next_data, parse_html

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "listings.json"


def _objects():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_objects_map_to_listings():
    listings = objects_to_listings(_objects())
    assert len(listings) == 4
    first = listings[0]
    assert first.code == 4055992
    assert first.rooms == 2
    assert first.storey == 32 and first.storeys == 32
    assert first.price == 4000 and first.currency == "USD"   # 840 → USD
    assert "Кальварийская" in first.address
    assert first.url == "https://realt.by/rent/flat-for-long/object/4055992/"


def test_free_text_gathers_prose_for_the_ai():
    first = objects_to_listings(_objects())[0]
    text = first.free_text()
    assert "пентхаус" in text.lower()          # title/headline present
    assert "Miele" in text                      # the nuance the LLM will extract


def test_is_top_floor_detection():
    first = objects_to_listings(_objects())[0]
    assert first.is_top_floor() is True         # storey 32 of 32


def test_extract_next_data_from_minimal_html():
    html = ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"objects":[{"code":1,"rooms":2,"price":500}]}}}'
            '</script></body></html>')
    data = extract_next_data(html)
    assert data["props"]["pageProps"]["objects"][0]["code"] == 1
    listings = parse_html(html)
    assert listings[0].code == 1 and listings[0].rooms == 2
