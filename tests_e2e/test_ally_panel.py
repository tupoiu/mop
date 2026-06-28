"""Ally summary panel end-to-end tests.

Drives the SPA against the stub backend (see ``conftest.py``) whose stubbed turn
streams an ``ally_metrics`` event (immediately) and an ``ally_summary`` event
(just before ``done``). Verifies that the sidebar Ally Panel populates from those
events, applies the warning visual state, and resets to a neutral empty state on
"New chat".

Covers requirements 1.1, 1.2, 1.4, 1.5, 6.1.
"""

import re

from playwright.sync_api import Page, expect


def _setup(page: Page, stub_url: str) -> None:
    """Navigate to the app and dismiss the in-memory token modal."""
    page.goto(f"{stub_url}/")
    page.locator("#token-input").fill("test-token")
    page.locator("#token-form button[type=submit]").click()
    expect(page.locator("#token-modal")).to_be_hidden()


def _send_stub_turn(page: Page) -> None:
    """Select the stub session and submit a message to trigger the stubbed turn."""
    sidebar_entry = page.locator("#session-list li").first
    expect(sidebar_entry).to_be_visible()
    sidebar_entry.click()

    page.locator("#composer-input").fill("tell me about quantum entanglement")
    page.locator("#composer button[type=submit]").click()


def test_ally_panel_populates_from_stubbed_summary(page: Page, stub_url: str) -> None:
    """After a stubbed turn the panel shows all four fields (Reqs 1.1, 1.2)."""
    _setup(page, stub_url)
    _send_stub_turn(page)

    panel = page.locator("#ally-panel")
    expect(panel).to_be_visible()

    # Topic and classification come from the ally_summary event.
    expect(page.locator("#ally-topic")).to_have_text("Quantum entanglement", timeout=5000)
    expect(page.locator("#ally-class")).to_have_text("Scientific")

    # UK time is server-supplied (here from the stub).
    expect(page.locator("#ally-time")).to_have_text("01:30")

    # Length renders in the `{A}/{U}W (A/U), {M}M` format from the stub summary
    # values (agent_words=2, user_words=2, message_count=2).
    expect(page.locator("#ally-length")).to_have_text("2/2W (A/U), 2M")


def test_ally_panel_warning_state(page: Page, stub_url: str) -> None:
    """A Scientific summary with warning:true turns the panel red (Req 6.1)."""
    _setup(page, stub_url)
    _send_stub_turn(page)

    panel = page.locator("#ally-panel")
    expect(panel).to_be_visible()

    # The stub summary sets warning:true, so the panel gains the `warning` class
    # and the warning sign (⚠) becomes visible.
    expect(panel).to_have_class(re.compile(r"\bwarning\b"), timeout=5000)
    expect(page.locator("#ally-warning")).to_be_visible()


def test_ally_panel_resets_on_new_chat(page: Page, stub_url: str) -> None:
    """"New chat" clears the panel back to the neutral empty state (Reqs 1.4, 1.5)."""
    _setup(page, stub_url)
    _send_stub_turn(page)

    # Wait for the panel to be populated before resetting so the assertion is
    # meaningful (we are clearing real values, not an already-empty panel).
    expect(page.locator("#ally-topic")).to_have_text("Quantum entanglement", timeout=5000)

    page.locator("#new-chat").click()

    # Fields are emptied, the warning class is removed, and the sign is hidden.
    expect(page.locator("#ally-topic")).to_have_text("")
    expect(page.locator("#ally-class")).to_have_text("")
    expect(page.locator("#ally-time")).to_have_text("")
    expect(page.locator("#ally-length")).to_have_text("")
    expect(page.locator("#ally-panel")).not_to_have_class(re.compile(r"\bwarning\b"))
    expect(page.locator("#ally-warning")).to_be_hidden()


def test_ally_panel_empty_on_fresh_load(page: Page, stub_url: str) -> None:
    """A fresh load (before any turn) shows the neutral empty state (Req 1.5)."""
    _setup(page, stub_url)

    expect(page.locator("#ally-panel")).to_be_visible()
    expect(page.locator("#ally-topic")).to_have_text("")
    expect(page.locator("#ally-class")).to_have_text("")
    expect(page.locator("#ally-time")).to_have_text("")
    expect(page.locator("#ally-length")).to_have_text("")
    expect(page.locator("#ally-panel")).not_to_have_class(re.compile(r"\bwarning\b"))
    expect(page.locator("#ally-warning")).to_be_hidden()
