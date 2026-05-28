"""SSE consumer end-to-end tests.

Drives the SPA against the stub backend that emits a recorded SSE sequence and
verifies that the frontend renders correctly: text deltas accumulate in the
assistant bubble, tool events render as collapsible <details> blocks, and no
further content appears after the `done` event.

Covers requirements 8.3, 8.4.
"""

from playwright.sync_api import Page, expect


def test_sse_consumer_renders_assistant_bubble_and_tool_blocks(page: Page, stub_url: str) -> None:
    page.goto(f"{stub_url}/")

    # Dismiss token modal
    page.locator("#token-input").fill("test-token")
    page.locator("#token-form button[type=submit]").click()
    expect(page.locator("#token-modal")).to_be_hidden()

    # Sidebar should list the stub session after load
    sidebar_entry = page.locator("#session-list li").first
    expect(sidebar_entry).to_be_visible()

    # Select the session so the composer is active
    sidebar_entry.click()

    # Submit a user message
    page.locator("#composer-input").fill("say hi")
    page.locator("#composer button[type=submit]").click()

    conv = page.locator("#conversation")

    # Assistant bubble accumulates both text deltas ("Hello " + "world")
    assistant_bubble = conv.locator(".bubble.assistant").first
    expect(assistant_bubble).to_have_text("Hello world", timeout=5000)

    # tool_call and tool_result render as <details class="tool-block">
    tool_details = conv.locator("details.tool-block")
    expect(tool_details).to_have_count(2)

    # First block is the tool call — summary names the tool
    expect(tool_details.first.locator("summary")).to_have_text("Tool: echo")

    # After done, no additional content is appended
    bubble_count = conv.locator(".bubble").count()
    page.wait_for_timeout(300)
    assert conv.locator(".bubble").count() == bubble_count, (
        "content continued to be appended after the done event"
    )
