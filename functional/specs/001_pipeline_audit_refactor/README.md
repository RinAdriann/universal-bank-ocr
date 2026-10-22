# Spec 001 — Core Extraction Pipeline Audit, Deduplication & Refactoring

- **Status:** Completed
- **Related Functional Requirements:** `FR-01`, `FR-02`, `FR-03`, `FR-04`, `FR-05`
- **Last Updated:** 2026-10-21

## Summary
Audited the Indonesian Bank Statement OCR extraction pipeline across `component/` and `app.py`, resolved hidden logic bugs and code duplication, removed dead dependencies (`pypdfium2`), centralized environment-backed configuration, and humanized variable names and control flow across all modules.

## Artifacts in This Spec
- [`analysis.md`](./analysis.md) — Root-cause audit of redundancies, bugs, dead code, and scalability issues.
- [`plan.md`](./plan.md) — Architecture & refactoring plan across modules.
- [`task.md`](./task.md) — Step-by-step execution checklist.
- [`fix.md`](./fix.md) — Detailed breakdown of code fixes and deduplications applied.
- [`walkthrough.md`](./walkthrough.md) — End-to-end verification and test walkthrough.
