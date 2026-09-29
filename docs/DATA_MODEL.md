# iV Data Model

## Core Principle

Separate memory, knowledge, projects, and system behavior.

---

# Users

Stores identity and preferences.

Fields:

- id
- preferences
- permissions
- created_at

---

# Conversations

Stores interactions.

Fields:

- id
- timestamp
- user_input
- iV_response
- agents_involved

---

# Memories

Long-term retained information.

Fields:

- id
- type
- content
- importance
- source
- created_at

Memory Types:

- Episodic
- Semantic
- Reflective

---

# Projects

Tracks real-world objectives.

Fields:

- id
- name
- description
- status
- milestones

---

# Tasks

Individual execution items.

Fields:

- id
- project_id
- description
- status
- priority

---

# Agents

Defines available agents.

Fields:

- id
- name
- role
- capabilities
- permissions

---

# Approvals

Tracks human authorization.

Fields:

- id
- action
- requester
- decision
- timestamp

---

# Improvements

Tracks self-development.

Fields:

- id
- proposed_change
- reason
- tests
- approval_status