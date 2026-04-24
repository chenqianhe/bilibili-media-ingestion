# Cadence Collaboration Files

This directory is the handoff surface for ongoing work that spans multiple turns or contributors.

Conventions:

- `tasks/`: living workboards for active initiatives. Update statuses in place.
- `progress/`: append-only progress logs with dated entries and validation results.
- `handoffs/`: current continuation notes for the next engineer or agent.

Update rules:

- Touch the relevant `tasks/` file before or during implementation when scope changes.
- Append to `progress/` after meaningful code or verification milestones.
- Refresh the paired `handoffs/` file before stopping so the next contributor can continue without replaying the chat.
