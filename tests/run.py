import importlib


def main() -> None:
    passed = 0
    for module_name in ("tests.test_app", "tests.test_health", "tests.test_metadata", "tests.test_jobs", "tests.test_filenames", "tests.test_http_policy", "tests.test_conversion_network", "tests.test_worker"):
        module = importlib.import_module(module_name)
        for name in sorted(dir(module)):
            if name.startswith("test_"):
                getattr(module, name)()
                print(f"PASS {module_name}.{name}")
                passed += 1
    print(f"{passed} tests passed")


if __name__ == "__main__":
    main()
