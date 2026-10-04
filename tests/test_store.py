import pytest

from store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def rid(store):
    pid = store.save_project({"name": "Demo"})
    return store.create_request(pid, "Title", "feature_add")


def test_project_save_and_find_case_insensitive(store):
    pid = store.save_project({"name": "  Demo ", "repo_path": "C:/src/demo"})
    found = store.find_project("demo")
    assert found["id"] == pid
    assert found["name"] == "Demo"
    assert found["repo_path"] == "C:/src/demo"
    assert store.find_project("missing") is None


def test_project_update(store):
    pid = store.save_project({"name": "Demo"})
    assert store.save_project({"name": "Renamed", "language": "Go"}, pid) == pid
    assert store.get_project(pid)["language"] == "Go"
    assert [p["name"] for p in store.list_projects()] == ["Renamed"]


def test_delete_project_cascades(store):
    pid = store.save_project({"name": "Demo"})
    rid = store.create_request(pid, "Title", "feature_add")
    store.add_version(rid, {"a": 1}, "prompt")
    store.delete_project(pid)
    assert store.get_request(rid) is None
    assert store.list_versions(rid) == []
    assert store.request_count(pid) == 0


def test_update_request_ignores_unknown_keys(store, rid):
    store.update_request(rid, status="Done", bogus="x", id=999)
    req = store.get_request(rid)
    assert req["id"] == rid
    assert req["status"] == "Done"
    store.update_request(rid, bogus="only unknown")  # no-op, no error
    assert store.get_request(rid)["status"] == "Done"


def test_add_version_round_trip(store, rid):
    state = {"template_id": "feature_add", "values": {"title": "T"}, "owners": {}, "team": []}
    vid = store.add_version(rid, state, "the prompt", edited=True, note="copied")
    ver = store.latest_version(rid)
    assert ver["id"] == vid
    assert ver["state"] == state
    assert ver["prompt"] == "the prompt"
    assert ver["edited"] is True
    assert ver["note"] == "copied"


def test_list_versions_oldest_first(store, rid):
    for n in range(3):
        store.add_version(rid, {"n": n}, f"p{n}")
    assert [v["prompt"] for v in store.list_versions(rid)] == ["p0", "p1", "p2"]
    assert store.latest_version(rid)["prompt"] == "p2"
    assert store.list_requests()[0]["version_count"] == 3


def test_latest_version_none_without_versions(store, rid):
    assert store.latest_version(rid) is None
