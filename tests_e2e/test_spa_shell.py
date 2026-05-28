"""Smoke test for the SPA shell: token modal appears, submits, hides, and no
auth state is persisted to localStorage, sessionStorage, or cookies.

Covers requirements 8.1, 8.2, 8.4 (Playwright shell coverage; in-memory-only
token; exposed via `poe test-e2e`).
"""

from playwright.sync_api import Page, expect


def test_spa_shell_token_modal_in_memory_only(page: Page, stub_url: str) -> None:
    page.goto(f"{stub_url}/")

    modal = page.locator("#token-modal")
    expect(modal).to_be_visible()

    page.locator("#token-input").fill("test-token")
    page.locator("#token-form button[type=submit]").click()

    expect(modal).to_be_hidden()

    local_keys = page.evaluate("() => Object.keys(localStorage)")
    session_keys = page.evaluate("() => Object.keys(sessionStorage)")
    cookies = page.context.cookies()

    assert local_keys == [], f"expected localStorage empty, got {local_keys}"
    assert session_keys == [], f"expected sessionStorage empty, got {session_keys}"
    assert cookies == [], f"expected no cookies, got {cookies}"
