# Source-led news packaging implementation plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task.

**Goal:** Render collected news footage, photographs and supplied notice screenshots inside a consistent animated news frame, with narration-bound shot timing.

**Architecture:** Reuse the existing source retrieval, platform case library, grounded writer and visual verifier. Apply a deterministic presentation layer after source selection. Split explicit `for_text` bindings into separately measured speech segments before encoding; do not stretch one subject across another subject's narration.

**Tech Stack:** Existing Python, Pillow, FFmpeg, local Ollama and Windows workbench.

**Spec:** User's 2026-11-16 clarification: collected original media inside, animated programme packaging outside; adapt platform writing structure; make shots correspond to narration. This replaces the proposed generative-video model approach. Style approval remains required before future automatic publication; this change does not publish.

## Constraints and review focus

- Preserve original footage and notice image proportions; no invented news scenes.
- Writing examples supply style only; source excerpts supply facts. Preserve existing reference provenance.
- Distinguish original document screenshots from rendered source excerpts.
- Reject missing, ambiguous or stale text-to-clip bindings instead of filling with another topic.
- Keep platform subtitle areas readable and animate outside the source viewport.
- Existing original-life/tutorial flows and old unbound sequences retain their behavior.
- Preserve the user's latest movie and drafts; temporary validation footage stays in disposable test directories.

## Tasks

- [x] Add regression tests for unequal narration durations, stale or incomplete bindings, and source/viewport preservation.
- [x] Implement exact-bound narration segmentation in `core/video_sequence.py`, connect news case preparation, preserve scene evidence.
- [x] Add `core/news_frame.py` and wire it into existing visual/video rendering, with per-shot source/time audit output and cache revision.
- [x] Verify platform writing references remain style-only; test the existing source-writing path.
- [x] Render one real-source sample, inspect frames and motion; report exactly what ran and what remains.
- [x] Run relevant integration checks before any package replacement.

## Verification and boundaries

- Full suite: 416 tests passed (2026-11-16, 48.328 s). Regression tests first reproduced incomplete clause coverage and stale mappings blocking reselection, then passed after correction.
- Staged Windows build passed `--check`: native WPF, 9 pages, local snapshot and bundled FFmpeg. Frozen worker archive contains `core.news_frame`, `core.video_sequence` and `core.grounded_news`.
- Read-only independent review caught portrait subtitle/source overlap and audit offsets reporting the selected range rather than the rendered excerpt. Both corrected; an actual ASS portrait stress render checked a wrapping subtitle and two-line title, with provenance now in a separate bottom note.
- Real-source sample uses retained Xinhua expo video and two original photographs, local Ollama copy and normal audio/FFmpeg rendering. It is a manually assembled packaging demonstration with model-generated photo narration, not an unattended production run or a new viral-style retrieval experiment.
- Local writer trials sometimes echoed instructions or mixed a neighboring object's qualifier. The retained sample isolates each photograph's facts when rewriting and checks the resulting text. No claim of model finetuning or globally improved writing quality is made.
- New selection rejects a bundle that omits any narration clause. Old incomplete saved bindings need reselection; the speech measurement step ignores stale bindings so this repair is possible. Existing rendered files remain unchanged.
- Supplied notice screenshots can be fitted as original images. Automatic official-webpage screenshot collection is not added in this slice; an original-text excerpt is explicitly labelled as such.
- No publishing, scheduled posting, or style approval was enabled.
- Installed into `dist/AutoContentFactory-Studio.exe`; installed WPF self-check passed and worker SHA-256 matches the checked package: `DDDC0C785FC23D78513CC6B8E5E74EBF9334E7C32746F13FB598C4EF437E1FC5`.
- Final sample: 26.83 seconds, full FFmpeg decode exit 0 with no reported errors.
- Cleanup of this run's sample intermediates and staged package directories was rejected before execution by automatic approval review (`blocked by policy`, no more specific reason supplied). Those files remain. The rejected deletion was not retried through another tool.
