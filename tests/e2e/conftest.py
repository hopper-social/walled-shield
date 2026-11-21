import pytest


@pytest.fixture(scope="session")
def pw():
    """One Playwright driver for the whole run (the sync API allows only one per thread)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    driver = sync_api.sync_playwright().start()
    yield driver
    driver.stop()
