from importlib import import_module


def test_package_imports():
    module = import_module("clankandclaw.main")
    assert hasattr(module, "main")


def test_config_module_imports():
    module = import_module("clankandclaw.config")
    assert hasattr(module, "load_config")
