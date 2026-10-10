from scripts.check_dangling_refs import check_source_imports


def test_flattened_entire_package_is_still_identified_as_local(tmp_path):
    (tmp_path / "release-allowlist.txt").write_text("forecasting/**\n*.py\n")
    (tmp_path / "main.py").write_text("from forecasting.regime_detection import classify\n")
    (tmp_path / "regime_detection.py").write_text("def classify(): return None\n")
    errors = check_source_imports(tmp_path)
    assert any("missing local module 'forecasting.regime_detection'" in error for error in errors)


def test_function_local_name_is_not_a_module_export(tmp_path):
    (tmp_path / "main.py").write_text("from helper import hidden\n")
    (tmp_path / "helper.py").write_text("def outer():\n    def hidden(): return 1\n    return hidden\n")
    assert any("missing symbol 'hidden'" in error for error in check_source_imports(tmp_path))


def test_alembic_migration_workspace_is_not_the_external_library(tmp_path):
    (tmp_path / "release-allowlist.txt").write_text("alembic/**\n")
    (tmp_path / "main.py").write_text("from alembic import op\nfrom alembic.config import Config\n")
    assert check_source_imports(tmp_path) == []
