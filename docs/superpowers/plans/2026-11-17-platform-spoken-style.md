# Platform spoken style implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. Preserve existing uncommitted project work.

**Goal:** A normal Windows workbench action selects a relevant, honestly labelled video reference, writes platform-specific narration, aligns sentence-sized shots and animation, and produces a reviewable hotspot video without publishing.

**Architecture:** Extend the existing reference library and video style catalogue, then carry the selected profile through the existing writer, visual director, render cache and native UI. Separate factual sources from writing/visual references. Reuse the local speech recognizer and existing media pipeline; no new mandatory paid services or large generation models.

**Tech Stack:** Python, SQLite, local Ollama, faster-whisper, Pillow/FFmpeg, WPF, existing Windows build.

**Spec:** User instruction approved on 2026-11-17; research evidence in `docs/research/2026-11-17-platform-ai-video-style-study.md`.

## Global constraints

- Do not publish. Run the installed workflow, not a manually assembled substitute.
- Preserve drafts, approved media and unrelated changes. Do not retry previously blocked cleanup.
- Report when a reference is a transcript, observed visuals, description only, or authored platform guidance.
- News facts must come from selected source passages, never reference examples.
- Keep existing user edits; invalidation regenerates automatic suggestions only.

## Review focus

- Photo notes and investment marketing cannot silently become spoken-video references.
- Old items/caches must not retain the mistaken reference after the new workflow runs.
- Unrelated topics may use labelled general platform guidance, never claim a matching viral example.
- Sentence splitting preserves attribution, numbers, source evidence and factual review.
- Offline reference lookup must still run with packaged, verified style observations.

## Tasks

- [ ] 1. Typed video-reference eligibility and supported platform profiles. Add rejection/regression tests; implement retrieval and provenance.
- [ ] 2. Carry writing rules and reference provenance into news prompts, revisions, sentence shots and visual direction; add meaningful integration tests.
- [ ] 3. Add executable animation decisions and varied staging tied to narration; verify decoded moving frames and no mismatched factual labels.
- [ ] 4. Surface the selected style, reference and applied method in WPF; build the unified package.
- [ ] 5. Use an ordinary installed action for one source-backed hotspot, inspect the generated narration and video, fix concrete defects, and report the result honestly.

## Execution ledger

- Ruling: Work on `codex/platform-spoken-style` in the existing workspace. The functioning application includes substantial uncommitted work from prior chats; a clean HEAD worktree would omit it. Only scoped edits will be made, with no blanket commit/reset/cleanup.
- Baseline: `chapter_cases()` accepts photo-note references for video; XHS lacks a video style catalogue; the retrieved-news prompt does not apply `video_style` rules as explicitly as the other writer path.
- Baseline: Real XHS spoken reference has nine local Whisper segments over 12.933 seconds; five short explanation units last 1.28–1.60 seconds. This timing is an observed short-form example, not a target for every platform or long-form narration.
