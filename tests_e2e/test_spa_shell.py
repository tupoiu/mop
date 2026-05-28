"""Smoke test for the SPA shell: token modal appears, submits, hides, and no
auth state is persisted to localStorage, sessionStorage, or cookies.

Covers requirements 8.1, 8.2, 8.4 (Playwright shell coverage; in-memory-only
token; exposed via `poe test-e2e`).
Also covers requirement 5.9 (Enter-to-send / Shift+Enter-for-newline).
"""

from playwright.sync_api import Page, expect


def _setup(page: Page, stub_url: str) -> None:
    """Navigate to the app and dismiss the token modal."""
    page.goto(f"{stub_url}/")
    page.locator("#token-input").fill("test-token")
    page.locator("#token-form button[type=submit]").click()
    expect(page.locator("#token-modal")).to_be_hidden()


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


def test_enter_submits_message(page: Page, stub_url: str) -> None:
    _setup(page, stub_url)

    # Select the stub session so the composer is active
    expect(page.locator("#session-list li").first).to_be_visible()
    page.locator("#session-list li").first.click()

    page.locator("#composer-input").fill("hello enter")
    page.locator("#composer-input").press("Enter")

    # A user bubble with the submitted text should appear
    expect(page.locator(".bubble.user").first).to_have_text("hello enter")


def test_shift_enter_inserts_newline_without_submitting(page: Page, stub_url: str) -> None:
    _setup(page, stub_url)

    composer = page.locator("#composer-input")
    composer.fill("line one")
    composer.press("Shift+Enter")

    # Textarea value should now contain a newline
    value = composer.input_value()
    assert "\n" in value, f"expected newline in textarea value, got {value!r}"

    # No user bubble should have been submitted
    assert page.locator(".bubble.user").count() == 0
