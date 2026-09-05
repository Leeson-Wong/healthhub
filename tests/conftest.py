from pathlib import Path

import pytest

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
CSV_PATH = Path(r"F:\health\data\health_data_20260819-23.csv")


@pytest.fixture
def resolver():
    from app.seeds import build_resolver_from_seed
    return build_resolver_from_seed(SEED_DIR)


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_path=str(tmp_path / "test.db"),
        seed_dir=str(SEED_DIR),
        scheduler_enabled=False,
        llm_api_key="",
        app_tz="Asia/Shanghai",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
