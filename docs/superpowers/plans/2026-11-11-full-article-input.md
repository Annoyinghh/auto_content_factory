# Selected news must reach the writer as article text

**Goal:** Preserve RSS as discovery metadata; resolve selected news to the publisher's actual article before writing.
**Architecture:** Keep the existing intake database, HTTP reader and editorial jobs. Mark feed excerpts, scope known publisher containers, and reuse the reader at source confirmation. No new account or media provider.
**Tech stack:** Python standard HTMLParser, requests, SQLite, existing WPF workbench.
**Spec:** User's hotspot → related sources → selected assembly → local batched writing. Existing works and settings remain intact. No publishing or desktop control.

## Task 1 — Evidence and regression tests
- [x] Pin RSS excerpts being incorrectly accepted as full reports.
- [x] Pin publisher-body extraction including short/final paragraphs and excluding unrelated footer content.
- [x] Pin selected-feed resolution and refusal when original reading fails.

## Task 2 — Minimal implementation
- [x] Add explicit feed/article provenance without changing the stored preview text contract.
- [x] Scope IT之家 and 中新网 to observed article containers; retain the generic reader for other publishers.
- [x] Keep feed candidates selectable; resolve only selected candidates before confirming their evidence.
- [x] Refresh matching legacy RSS records without overwriting previously read originals.
- [x] Apply the same original-read rule to automatic hotspot copywriting.

## Task 3 — Proof and delivery
- [x] Read real current originals through the production reader and verify body completeness and metadata.
- [x] Run relevant regressions and the existing full test suite (272 passed).
- [x] Build and verify the one unified Windows package, preserving settings and local models.

Proof: `build/platform-longform-20261111/frozen-article-input-proof.json` records the actual installed launcher resolving three observed feeds into 731/3667/1044-character originals. The isolated creation flow succeeded; its original verifier then failed while cleaning an open SQLite connection. The connection handling was corrected, but approval review refused the explicit recursive fixture cleanup (`blocked by policy`). The 71,488-byte residual fixture is retained, and the verifier no longer attempts automatic deletion. No user draft or source was deleted.

Remaining outside this slice: full B站/抖音 video-reference transcription; original-news media extraction for additional publishers; multi-topic 10–15 minute finished video and chapter reader acceptance. Do not present these as complete.
