"""Pagination params must be bounded (no LIMIT -1 = whole-table dumps)."""
import pytest


@pytest.mark.parametrize("qs", ["per_page=-1", "per_page=0", "page=0", "page=-5", "per_page=100000"])
def test_out_of_range_params_rejected(client, auth, qs):
    r = client.get(f"/api/images?{qs}", headers=auth)
    assert r.status_code == 422


def test_valid_pagination_ok(client, auth):
    r = client.get("/api/images?page=1&per_page=10", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["images"] == []
