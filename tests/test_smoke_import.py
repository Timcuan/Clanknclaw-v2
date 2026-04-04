from importlib import import_module
from pytest import raises


def test_package_imports():
    module = import_module("clankandclaw.main")
    assert hasattr(module, "main")


def test_main_exits_with_bootstrap_message():
    module = import_module("clankandclaw.main")

    with raises(SystemExit, match="bootstrap only"):
        module.main()
