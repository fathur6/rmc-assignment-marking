---
name: rmc-assignment-marking
description: Mark PPS10203 Research Methodology (RMC) assignments at scale. Grades each manuscript against the official five-criterion rubric (CLO2 40% + CLO3 20% = 60%), returns an annotated manuscript PDF with criterion-tagged margin comments and a filled marking form with a computed summary page, and maintains a gradebook plus review dashboard.
whenToUse: Use when the user asks to mark, grade, moderate, check, or batch-process PPS10203 / RMC assignment manuscripts or proposals; when producing annotated manuscripts or filled RMC marking forms; when reviewing an RMC gradebook, flagged scripts, or marks out of 60; or when setting up the RMC Assignment Marking Module.
---

# RMC Assignment Marking Module

Deterministic pipeline around a rubric-anchored model judgement. The module lives at
`~/.dsh/skills/rmc-assignment-marking`. Run it with its bundled environment; do not
install packages or reimplement the PDF work in the conversation.

The module is on your PATH as `rmc` (launcher at `~/.local/bin/rmc`). If it is missing, run
the bundled entry point directly: `~/.dsh/skills/rmc-assignment-marking/bin/rmc`.

```sh
rmc doctor
```

The everyday routine needs no flags: put PDFs in `~/Documents/RMC-Marking/inbox`, run
`rmc batch`, and collect results from `~/Documents/RMC-Marking/marked`.

`doctor` must print an API key, a passing API probe, and existing template/calibration
paths before marking. The key comes from `DEEPSEEK_API_KEY` or the DSH credential store.

## What one marked manuscript produces

Each student gets a folder named `<matric> - <name>` under the output root:

| File | Contents |
|---|---|
| `<matric> - <name> - Annotated assignment.pdf` | Original pages, red highlights at each quoted passage, and red sticky notes authored by the evaluator with `[CLOn - Category]` feedback. |
| `<matric> - <name> - Marking form.pdf` | Official marking form filled with name, matric, evaluator and the five marks, plus an appended A4 summary page with CLO raw/weighted marks, total /60, percentage, strengths and priority improvements. |
| `result.json` | Validated model payload, evidence, comments, computed marks, model, token usage, issues, source hash. This is the audit record and the regeneration input. |

The output root also gets `gradebook.csv`, `review.html` and `batch-summary.json`.

## Workflow

1. **Confirm scope.** Course PPS10203, assignment weight 60% (final exam 40%). The module
   marks the assignment only. Marks are provisional until the evaluator confirms them.
2. **Calibrate once.** Always dry-run the first manuscript before a large batch and read the
   five marks, evidence and comments:
   ```sh
   rmc mark "/path/to/one.pdf" --dry-run
   ```
   A complete-but-generic proposal should land near 6/5/5/6/5 = 32.33/60 (53.9%).
   If marks are systematically low or high, stop and report the drift to the user instead
   of silently accepting it; the rubric prompt lives in `engine/grade.py` and
   `prompts/calibration.json`.
3. **Batch mark.**
   ```sh
   rmc batch                                  # configured inbox
   rmc batch "/path/to/manuscripts" --out "/path/to/output" --evaluator "Evaluator Name"
   ```
   Finished scripts are skipped on rerun by source hash; use `--overwrite` only when asked.
   `--limit N` marks a trial subset. The run is resumable: stop it and rerun the same command.
4. **Review the queue, not every script.** Open `review.html`. Rows are ordered with
   flagged cases first. Triage:
   - Yellow (`needs_review`): low confidence, unmatched quote, missing name/matric, too few
     comments, truncated input, wrong-file suspicion. Read the script and decide.
   - Red (below 24/60, i.e. under 40%): check for a genuinely weak proposal versus an
     extraction failure before releasing.
   - Spot-check a random 5-10% of clean rows for calibration drift.
5. **Fix without re-grading.** After editing `result.json` (for example correcting a name or
   a comment), rebuild only the PDFs:
   ```sh
   rmc regen "/path/to/output/<student>/result.json" --evaluator "Evaluator Name"
   ```
6. **Confirm final marks.** Only after the evaluator accepts the marks:
   ```sh
   rmc finalize "/path/to/output/<student>/result.json" --by "Evaluator Name"
   ```
   This stamps the form footer with the confirmation and marks the gradebook row FINAL.
   Use `--name` when a title page omits the student name.
7. **Hand back.** Point the user to the output root, `gradebook.csv` and `review.html`.
   State counts, mean, flagged count, and any calibration concern.

## Marking standard

- Five criteria, each 0-10: organisation and coherence; clarity and alignment; use of
  literature (CLO2); academic integrity and citation; professional practice and ethics
  (CLO3).
- CLO2 = subtotal / 30 x 40; CLO3 = subtotal / 20 x 20; assignment total /60.
- Bands: 0-1 very weak, 2-3 weak, 4-5 satisfactory, 6-8 good, 9-10 excellent.
- The model must quote an exact anchor for each margin comment; unmatched anchors are
  dropped and flagged, never silently invented.
- Never present the module's mark as final, never invent a student's identity, and never
  mark a file whose text extraction is empty or clearly the wrong document.

## Operational limits

- One manuscript takes roughly 45-70 seconds on `deepseek-flash` (~15-20k tokens) at four
  workers; 500 scripts take about two hours. Prefer `deepseek-flash` unless the user asks
  for `deepseek-v4-pro`; set `marking.model` in `config.yaml`.
- The model sees extracted text only, not scanned images. A scanned or image-only PDF
  extracts little text: flag it for OCR/manual marking rather than grading it.
- English, Malay, and Indonesian proposals are accepted; feedback follows the manuscript's
  language. Theses, dissertations, and documents without proposal sections are refused.
- The form template, evaluator name, semester and weights are configuration, not code:
  edit `config.yaml`, not the engine.

## Troubleshooting

| Symptom | Action |
|---|---|
| `No API key` | Set `DEEPSEEK_API_KEY` in the environment or add it to `~/.dsh/.credentials.yaml` refs. |
| HTTP 402 | Balance exhausted; tell the user; do not retry in a loop. |
| Most comments unmatched | Extraction produced garbled text; mark that script for manual review. |
| JSON repair failure | Rerun that single file with `mark`; if it repeats, lower concurrency and report. |
| Form looks misaligned | The template changed; restore `assets/marking-form.pdf` from the official form. |
