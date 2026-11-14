"""Oracle for the contracts shipped inside the starter, never shown to an arm."""
from copy import deepcopy

import pytest

from servicecfg.launch import plan_launches
from servicecfg.report import service_report
from servicecfg.resolver import resolve_service


def doc(profiles=None, service=None):
    return {"profiles": profiles or {}, "services": {"api": service or {}}}


def resolve(profiles=None, service=None):
    return resolve_service(doc(profiles, service), "api")


def test_complete_factory_result_without_profiles_section():
    assert resolve_service({"services": {"api": {}}}, "api") == {
        "enabled": True, "workers": 2, "timeout": 30,
        "retry": {"attempts": 2, "delays": [1, 3]}, "labels": {},
    }


def test_chain_and_service_override_are_sparse():
    profiles = {"base": {"workers": 7, "labels": {"team": "ops"}},
                "edge": {"extends": ["base"], "retry": {"attempts": 5}}}
    value = resolve(profiles, {"extends": ["edge"], "timeout": 9})
    assert value["workers"] == 7
    assert value["timeout"] == 9
    assert value["retry"] == {"attempts": 5, "delays": [1, 3]}
    assert value["labels"] == {"team": "ops"}
    assert set(value) == {"enabled", "workers", "timeout", "retry", "labels"}


def test_unrelated_later_profile_does_not_reintroduce_defaults():
    value = resolve({"sized": {"workers": 9, "retry": {"attempts": 8}},
                     "tagged": {"labels": {"zone": "west"}}},
                    {"extends": ["sized", "tagged"]})
    assert value["workers"] == 9
    assert value["retry"]["attempts"] == 8
    assert value["labels"] == {"zone": "west"}


def test_later_explicit_factory_values_do_override():
    value = resolve({"large": {"workers": 9, "timeout": 80},
                     "normal": {"workers": 2}}, {"extends": ["large", "normal"]})
    assert value["workers"] == 2
    assert value["timeout"] == 80


def test_profile_order_changes_only_conflicting_fields():
    profiles = {"one": {"workers": 7, "timeout": 40},
                "two": {"workers": 4, "retry": {"attempts": 0}}}
    forward = resolve(profiles, {"extends": ["one", "two"]})
    reverse = resolve(profiles, {"extends": ["two", "one"]})
    assert forward["workers"] == 4
    assert reverse["workers"] == 7
    assert forward["timeout"] == reverse["timeout"] == 40
    assert forward["retry"] == reverse["retry"] == {"attempts": 0, "delays": [1, 3]}


def test_diamond_applies_shared_ancestor_once():
    profiles = {"base": {"workers": 4, "timeout": 60},
                "left": {"extends": ["base"], "workers": 8},
                "right": {"extends": ["base"], "timeout": 10}}
    value = resolve(profiles, {"extends": ["left", "right"]})
    assert value["workers"] == 8
    assert value["timeout"] == 10


def test_duplicate_and_explicit_ancestor_do_not_replay():
    profiles = {"base": {"workers": 4},
                "child": {"extends": ["base"], "workers": 8}}
    assert resolve(profiles, {"extends": ["base", "child", "base"]})["workers"] == 8
    assert resolve(profiles, {"extends": ["child", "base", "child"]})["workers"] == 8


def test_diamond_does_not_restore_removed_labels():
    profiles = {"base": {"labels": {"owner": "old", "zone": "a"}},
                "left": {"extends": ["base"], "labels": {"owner": None}},
                "right": {"extends": ["base"], "labels": {"zone": "b"}}}
    assert resolve(profiles, {"extends": ["left", "right"]})["labels"] == {"zone": "b"}


def test_profile_names_are_not_service_names():
    document = {"profiles": {"api": {"workers": 7}},
                "services": {"api": {"extends": ["api"]}}}
    assert resolve_service(document, "api")["workers"] == 7
    with pytest.raises(ValueError):
        resolve_service({"services": {"api": {"extends": ["other"]}, "other": {}}}, "api")


def test_scalar_resets_use_factory_and_preserve_zero_false():
    profiles = {"base": {"workers": 11, "timeout": 50, "enabled": False},
                "reset": {"workers": None, "timeout": None}}
    value = resolve(profiles, {"extends": ["base", "reset"], "timeout": 0})
    assert value["workers"] == 2
    assert value["timeout"] == 0
    assert value["enabled"] is False
    assert resolve(profiles, {"extends": ["base"], "enabled": None})["enabled"] is True


def test_whole_retry_reset_followed_by_sparse_edit():
    profiles = {"base": {"retry": {"attempts": 9, "delays": [20, 40]}},
                "reset": {"retry": None},
                "tweak": {"retry": {"attempts": 1}}}
    value = resolve(profiles, {"extends": ["base", "reset", "tweak"]})
    assert value["retry"] == {"attempts": 1, "delays": [1, 3]}


def test_retry_field_reset_preserves_sibling_and_arrays_replace():
    profiles = {"base": {"retry": {"attempts": 7, "delays": [9, 18, 27]}},
                "reset": {"retry": {"attempts": None}}}
    assert resolve(profiles, {"extends": ["base", "reset"]})["retry"] == {
        "attempts": 2, "delays": [9, 18, 27]}
    assert resolve(profiles, {"extends": ["base"], "retry": {"delays": []}})["retry"] == {
        "attempts": 7, "delays": []}
    assert resolve(profiles, {"extends": ["base"], "retry": {"delays": [0]}})["retry"] == {
        "attempts": 7, "delays": [0]}
    assert resolve(profiles, {"extends": ["base"], "retry": {"delays": None}})["retry"] == {
        "attempts": 7, "delays": [1, 3]}


def test_label_tombstone_is_an_edit_even_without_a_local_value():
    profiles = {"owner": {"labels": {"owner": "old", "zone": "west"}},
                "unowned": {"labels": {"owner": None, "absent": None}}}
    value = resolve(profiles, {"extends": ["owner", "unowned"]})
    assert value["labels"] == {"zone": "west"}


def test_whole_labels_reset_and_reintroduction():
    profiles = {"base": {"labels": {"a": "old", "b": "old"}},
                "clear": {"labels": None}, "new": {"labels": {"c": ""}}}
    assert resolve(profiles, {"extends": ["base", "clear", "new"]})["labels"] == {"c": ""}
    assert resolve(profiles, {"extends": ["base", "clear"], "labels": {"a": "new"}})["labels"] == {"a": "new"}


def test_empty_mappings_inherit():
    profiles = {"base": {"retry": {"attempts": 5, "delays": [8]}, "labels": {"a": "x"}}}
    value = resolve(profiles, {"extends": ["base"], "labels": {}, "retry": {}})
    assert value["labels"] == {"a": "x"}
    assert value["retry"] == {"attempts": 5, "delays": [8]}


def test_validate_completed_result_not_partial_profiles():
    profiles = {"template": {"workers": 0, "retry": {"attempts": "fill-me", "delays": "fill-me"}},
                "production": {"extends": ["template"], "workers": 3,
                               "retry": {"attempts": 1, "delays": []}}}
    assert resolve(profiles, {"extends": ["production"]})["workers"] == 3
    value = resolve(profiles, {"extends": ["template"], "workers": None, "retry": None})
    assert value["workers"] == 2
    assert value["retry"] == {"attempts": 2, "delays": [1, 3]}


def test_later_sibling_can_repair_an_intermediate_value():
    value = resolve({"placeholder": {"timeout": -1}, "fix": {"timeout": 0}},
                    {"extends": ["placeholder", "fix"]})
    assert value["timeout"] == 0


@pytest.mark.parametrize("layer", [
    {"workers": 0}, {"workers": True}, {"workers": 1.5},
    {"timeout": -1}, {"timeout": False}, {"enabled": 1},
    {"retry": {"attempts": -1}}, {"retry": {"attempts": True}},
    {"retry": {"delays": [-1]}}, {"retry": {"delays": [False]}},
    {"retry": {"delays": "1,3"}}, {"labels": {"a": 2}}, {"labels": {7: "x"}},
])
def test_invalid_completed_values_raise_value_error(layer):
    with pytest.raises(ValueError):
        resolve({"invalid": layer}, {"extends": ["invalid"]})


@pytest.mark.parametrize("layer", [
    {"unknown": 1}, {"retry": {"unknown": 1}}, {"retry": []}, {"labels": []},
    {"extends": "base"}, {"extends": [1]}, {"extends": None},
])
def test_invalid_visited_layer_shapes_raise_value_error(layer):
    with pytest.raises(ValueError):
        resolve({"broken": layer}, {"extends": ["broken"]})


def test_cycle_and_missing_parent_raise_value_error():
    for profiles, parents in [({"a": {"extends": ["a"]}}, ["a"]),
                              ({"a": {"extends": ["b"]}, "b": {"extends": ["a"]}}, ["a"]),
                              ({"a": {"extends": ["missing"]}}, ["a"]),
                              ({}, ["missing"])]:
        with pytest.raises(ValueError):
            resolve(profiles, {"extends": parents})


def test_unrelated_broken_profiles_and_services_are_not_visited():
    document = {"profiles": {"cycle": {"extends": ["cycle"]}, "broken": {"workers": 0},
                              "good": {"workers": 6}},
                "services": {"api": {"extends": ["good"]}, "other": {"extends": ["missing"]}}}
    assert resolve_service(document, "api")["workers"] == 6
    with pytest.raises(KeyError):
        resolve_service(document, "absent")


def test_visited_invalid_shapes_cannot_be_repaired_by_a_descendant():
    with pytest.raises(ValueError):
        resolve({"broken": {"retry": {"extra": 1}}},
                {"extends": ["broken"], "retry": None})


def test_each_service_gets_its_own_visit_history():
    document = {"profiles": {"base": {"workers": 8, "labels": {"a": "base"}}},
                "services": {"a": {"extends": ["base"], "labels": {"a": "first"}},
                             "b": {"extends": ["base"]}}}
    assert resolve_service(document, "a")["workers"] == 8
    assert resolve_service(document, "b")["labels"] == {"a": "base"}
    rows = plan_launches(document)
    assert [row["workers"] for row in rows] == [8, 8]
    assert [row["labels"] for row in rows] == [{"a": "first"}, {"a": "base"}]


def test_purity_return_isolation_and_no_stale_cache():
    document = doc({"base": {"retry": {"delays": [7]}, "labels": {"team": "ops"}}},
                   {"extends": ["base"]})
    before = deepcopy(document)
    first = resolve_service(document, "api")
    second = resolve_service(document, "api")
    first["retry"]["delays"].append(8)
    first["labels"]["team"] = "edited"
    assert document == before
    assert second["retry"]["delays"] == [7]
    assert second["labels"] == {"team": "ops"}
    document["profiles"]["base"]["retry"]["delays"].append(9)
    assert resolve_service(document, "api")["retry"]["delays"] == [7, 9]
    assert second["retry"]["delays"] == [7]
    assert resolve()["retry"]["delays"] == [1, 3]


def consumer_document():
    return {"profiles": {"shared": {"workers": 5, "timeout": 0, "retry": {"attempts": 0, "delays": []},
                                        "labels": {"owner": "ops"}},
                         "off": {"extends": ["shared"], "enabled": False}},
            "services": {"z": {"extends": ["off"]}, "a": {"extends": ["shared"]},
                         "B": {"extends": ["off"], "enabled": True}}}


def test_launch_consumer_effective_values_order_and_disabled_filter():
    assert plan_launches(consumer_document()) == [
        {"service": name, "workers": 5, "timeout": 0,
         "retry": {"attempts": 0, "delays": []}, "labels": {"owner": "ops"}}
        for name in ["B", "a"]]


def test_report_consumer_effective_values_and_disabled_visibility():
    assert service_report(consumer_document()) == [
        {"service": name, "status": status, "workers": 5, "timeout": 0,
         "retry_attempts": 0, "labels": {"owner": "ops"}}
        for name, status in [("B", "ready"), ("a", "ready"), ("z", "disabled")]]


def test_consumer_nested_results_are_independent():
    document = consumer_document()
    before = deepcopy(document)
    launches = plan_launches(document)
    report = service_report(document)
    launches[0]["labels"]["owner"] = "modified"
    launches[0]["retry"]["delays"].append(99)
    report[0]["labels"]["owner"] = "also modified"
    assert document == before
    assert launches[1]["labels"] == {"owner": "ops"}
    assert launches[1]["retry"]["delays"] == []
    assert report[1]["labels"] == {"owner": "ops"}
    assert service_report(document)[0]["labels"] == {"owner": "ops"}


@pytest.mark.parametrize("consumer", [plan_launches, service_report])
def test_disabled_configuration_errors_propagate(consumer):
    with pytest.raises(ValueError):
        consumer(doc({}, {"enabled": False, "extends": ["missing"]}))
    with pytest.raises(ValueError):
        consumer(doc({}, {"enabled": False, "workers": 0}))


def test_consumers_do_not_hide_multi_profile_errors():
    document = doc({"large": {"workers": 8}, "bad": {"extends": ["missing"]}},
                   {"extends": ["large", "bad"]})
    with pytest.raises(ValueError):
        plan_launches(document)
    with pytest.raises(ValueError):
        service_report(document)
