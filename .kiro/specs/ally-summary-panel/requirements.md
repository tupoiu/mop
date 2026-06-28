# Requirements Document

## Project Description (Input)
Ally summary panel — Whenever a message is sent in the chat, display an "ally" panel (the AI should act as an ally, not an impartial party) that summarizes the current conversation. The panel contains: (1) a short topic summary generated concurrently by a small model (e.g. Claude Haiku) spun out as a side agent — e.g. "ethical hdmi cable purchasing follow up"; (2) a classification of the conversation into a finite set: Programming-adjacent, Philosophical, Scientific, Other; (3) the current time in the UK (computed in Python); (4) the conversation length shown as words and messages, e.g. "10000/300W (A/U), 50M" meaning 10000 agent words, 300 user words, 50 messages. Colour logic: if the topic is programming-adjacent, scientific, or otherwise unhelpful AND it's getting late, render the panel red with a warning sign; otherwise render it a neutral or positive colour. Scope: v1 is the basic panel only — the "extensions" ideas (eating/walking reminders, dependence-awareness leading questions) are a separate future spec and out of scope here.

## Introduction
The Ally Panel is a sidebar element that frames the assistant as the user's ally rather than an impartial party. It gives the user an at-a-glance read on what the conversation is about, how it is classified, the current UK time, and how long the conversation has run — and it turns red with a warning sign when the user appears to be doing unhelpful work (programming or scientific) late at night. The intent is gentle self-awareness: nudging the user to notice when it is getting late and the session may no longer be serving them.

This specification covers only the baseline panel. Wellbeing extensions (eating/walking reminders, dependence-awareness questions) are deliberately deferred to a separate future specification.

## Boundary Context
- **In scope**: a sidebar panel showing topic summary, conversation classification, current UK time, and conversation length metrics; the red/neutral colour logic; a server-configurable "late" threshold; updates on both user send and assistant turn completion; graceful degradation when summary/classification cannot be produced.
- **Out of scope**: wellbeing extensions (eating, walking, dependence-awareness prompts); persistence of panel state across page reloads; a GUI for editing the late threshold; any alerting beyond the panel's colour and warning sign; changing the assistant's actual replies or behaviour.
- **Adjacent expectations**: the panel relies on the existing conversation turn lifecycle (user send → streamed assistant turn → turn completion) and the existing stored message history as the source for length metrics. The topic summary and classification are expected to be produced by a secondary, lighter-weight generation path; the specific model and mechanism are a design decision, not a requirement.

## Requirements

### Requirement 1: Ally Panel presence and placement
**Objective:** As a user, I want a persistent ally panel in the sidebar, so that I can see a supportive read on my current conversation at a glance.

#### Acceptance Criteria
1. The Ally Panel shall be displayed in the sidebar, adjacent to the session list.
2. While a session is active, the Ally Panel shall display four fields: the topic summary, the conversation classification, the current UK time, and the conversation length metrics.
3. The Ally Panel shall frame the assistant as the user's ally, using neutral or positive presentation except when the warning state defined in Requirement 6 applies.
4. The Ally Panel state shall be ephemeral and shall not be persisted across page reloads.
5. When a session is opened or reloaded before any new turn has occurred, the Ally Panel shall display a neutral empty state rather than stale values.

### Requirement 2: Topic summary
**Objective:** As a user, I want a short topic summary of my conversation, so that I can recognise at a glance what the session is currently about.

#### Acceptance Criteria
1. When an assistant turn completes, the system shall produce a concise topic summary (a short phrase of a few words) describing the current conversation and display it in the Ally Panel.
2. The system shall generate the topic summary concurrently so that it does not delay or block the streamed assistant response.
3. If topic summary generation fails or is unavailable, the Ally Panel shall display a neutral placeholder for the topic and shall continue to display the remaining fields, and the assistant turn shall be unaffected.

### Requirement 3: Conversation classification
**Objective:** As a user, I want my conversation classified into a known category, so that the panel can reason about whether the activity is helpful late at night.

#### Acceptance Criteria
1. When the Ally Panel is computed, the system shall classify the conversation into exactly one of: Programming-adjacent, Philosophical, Scientific, Other.
2. The Ally Panel shall display the resulting classification.
3. If classification fails or is unavailable, the system shall treat the conversation as `Other` for the purposes of display and colour logic.

### Requirement 4: Current UK time
**Objective:** As a user, I want the current UK time shown in the panel, so that I am reminded of the actual hour regardless of my system clock.

#### Acceptance Criteria
1. The Ally Panel shall display the current time in the United Kingdom (Europe/London), computed server-side.
2. The system shall reflect UK daylight rules (BST/GMT) so that the displayed time matches UK wall-clock time at any date.

### Requirement 5: Conversation length metrics
**Objective:** As a user, I want to see how long my conversation has grown, so that I have an objective sense of how much I have invested in the session.

#### Acceptance Criteria
1. The Ally Panel shall display conversation length in the format `{A}/{U}W (A/U), {M}M`, where `A` is total agent (assistant) words, `U` is total user words, and `M` is the message count.
2. The system shall compute `A` as the total number of words across assistant text messages and `U` as the total number of words across user text messages in the current session.
3. The system shall compute `M` as the number of user and assistant messages in the current session.
4. The system shall exclude tool-call and tool-result entries from the word counts `A` and `U`.
5. When the user sends a message, the system shall update the length metrics immediately so that the just-sent user message is reflected before the assistant reply completes.

### Requirement 6: Colour and warning state
**Objective:** As a user, I want the panel to turn red with a warning when I am doing unhelpful work late at night, so that I am nudged to notice and stop.

#### Acceptance Criteria
1. If the current UK time is within the late window (Requirement 7) and the classification is Programming-adjacent or Scientific, then the Ally Panel shall render in red and display a warning sign.
2. While the conversation is not in the warning state defined above, the Ally Panel shall render in a neutral or positive colour and shall not display a warning sign.
3. While the classification is Philosophical or Other, the Ally Panel shall not enter the warning state regardless of the time.
4. While the current UK time is outside the late window, the Ally Panel shall not enter the warning state regardless of the classification.

### Requirement 7: Configurable late window
**Objective:** As an operator, I want to configure the late window in which warnings can fire, so that it matches my own schedule.

#### Acceptance Criteria
1. The system shall determine the late window from a single server-side configuration value (for example, one environment variable) that encodes both a start and an end UK local time (for example, `21:30-05:00`).
2. Where the late-window configuration is unset or invalid, the system shall default the late window to 21:30–05:00 UK time and continue operating.
3. When the configured window crosses midnight (the end time is earlier in the day than the start time), the system shall treat the window as spanning from the start time on one day to the end time on the next day.

### Requirement 8: Panel update lifecycle
**Objective:** As a user, I want the panel to stay current as I interact, so that what it shows reflects the live state of the conversation.

#### Acceptance Criteria
1. When the user sends a message, the system shall update the conversation length metrics for the Ally Panel.
2. When the assistant turn completes, the system shall refresh the topic summary, classification, UK time, length metrics, and colour state in the Ally Panel.
3. While an assistant turn is streaming, the system shall not block or delay the streamed response in order to compute Ally Panel fields.
