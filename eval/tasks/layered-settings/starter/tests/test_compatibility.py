from copy import deepcopy

from servicecfg.launch import plan_launches
from servicecfg.report import service_report
from servicecfg.resolver import resolve_service


def test_factory_settings():
    assert resolve_service({"services": {"api": {}}}, "api") == {
        "enabled": True, "workers": 2, "timeout": 30,
        "retry": {"attempts": 2, "delays": [1, 3]}, "labels": {},
    }


def test_inline_values_and_empty_delay_list():
    layer = {"workers": 4, "timeout": 0, "enabled": False,
             "retry": {"attempts": 0, "delays": []}, "labels": {"team": ""}}
    assert resolve_service({"services": {"api": layer}}, "api") == layer


def test_inline_nulls_restore_factory_values():
    value = resolve_service({"services": {"api": {"workers": None,
                            "retry": {"attempts": None}, "labels": None}}}, "api")
    assert value["workers"] == 2
    assert value["retry"] == {"attempts": 2, "delays": [1, 3]}
    assert value["labels"] == {}


def test_return_values_do_not_alias_document_or_factory():
    document = {"services": {"api": {"labels": {"team": "web"}}}}
    before = deepcopy(document)
    first = resolve_service(document, "api")
    first["labels"]["team"] = "edited"
    first["retry"]["delays"].append(20)
    assert document == before
    assert resolve_service(document, "api")["retry"]["delays"] == [1, 3]


def test_inline_consumer_formats_and_order():
    document = {"services": {"z": {"enabled": False}, "a": {"workers": 4}}}
    assert plan_launches(document) == [{"service": "a", "workers": 4, "timeout": 30,
        "retry": {"attempts": 2, "delays": [1, 3]}, "labels": {}}]
    assert service_report(document) == [
        {"service": "a", "status": "ready", "workers": 4, "timeout": 30,
         "retry_attempts": 2, "labels": {}},
        {"service": "z", "status": "disabled", "workers": 2, "timeout": 30,
         "retry_attempts": 2, "labels": {}},
    ]
