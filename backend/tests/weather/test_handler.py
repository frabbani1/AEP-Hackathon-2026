"""Lambda handler: the weather route, 503 without a zones file, and the health fields."""
import json

import pytest

from concierge.api import handler

from .conftest import ZONE_A


def event(method, path, body=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body) if body else ""}


def use(monkeypatch, svc):
    monkeypatch.setattr(handler, "_service", svc)


def test_weather_route_works_with_and_without_the_api_prefix(monkeypatch, make_svc):
    use(monkeypatch, make_svc())
    for path in ("/weather", "/api/weather"):
        res = handler.lambda_handler(event("POST", path, {"zone_id": ZONE_A}))
        assert res["statusCode"] == 200 and json.loads(res["body"])["status"] == "ok"


def test_weather_is_post_only(monkeypatch, make_svc):
    use(monkeypatch, make_svc())
    assert handler.lambda_handler(event("GET", "/weather"))["statusCode"] == 405


@pytest.mark.parametrize("path,body", [("/zones", {}), ("/zones", {"severity": "weather"}),
                                       ("/zones/assess", {"zone_id": ZONE_A}), ("/weather", {"zone_id": ZONE_A}),
                                       ("/api/zones", {})])
def test_map_routes_answer_503_without_a_zones_file(monkeypatch, make_svc, path, body):
    use(monkeypatch, make_svc(zones=False))
    res = handler.lambda_handler(event("POST", path, body))
    assert res["statusCode"] == 503 and json.loads(res["body"])["error"]["code"] == "unavailable"


def test_health_reports_weather_and_zones(monkeypatch, make_svc):
    use(monkeypatch, make_svc())
    body = json.loads(handler.lambda_handler(event("GET", "/health"))["body"])
    assert body["weather"] == "on" and body["zones"] == 3
    use(monkeypatch, make_svc(weather="off", zones=False))
    body = json.loads(handler.lambda_handler(event("GET", "/health"))["body"])
    assert body["weather"] == "off" and body["zones"] is None


def test_bad_as_of_is_a_400_not_a_500(monkeypatch, make_svc):
    use(monkeypatch, make_svc())
    res = handler.lambda_handler(event("POST", "/zones", {"severity": "weather", "as_of": "tomorrow"}))
    assert res["statusCode"] == 400


def test_zone_files_with_label_or_name_both_load():
    from concierge.zones.index import ZoneIndex
    from .conftest import zone_doc
    new, old = zone_doc(), zone_doc()
    for z in new["zones"]:
        z["label"] = z.pop("name")
    assert [z.name for z in ZoneIndex.from_dict(new)] == [z.name for z in ZoneIndex.from_dict(old)] == ["Area 1", "Area 2", "Area 3"]
    for z in old["zones"]:
        del z["name"]
    with pytest.raises(Exception):
        ZoneIndex.from_dict(old)
