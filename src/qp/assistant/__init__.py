"""qp.assistant — Read-only LLM assistant over qp functions. Never computes numbers itself; it
calls qp functions and cites the data used (rule 12).

Built in phase 9 (ADR 0016): read-only tools, a tool loop and a number guard.
May import: everything below. See docs/ARCHITECTURE.md and .importlinter.
"""
