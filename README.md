# RMC Assignment Marking Module

A marking pipeline for **PPS10203 Research Methodology (RMC)** assignments — the 60%
continuous-assessment component; the final examination is the other 40%. It marks a folder
of manuscripts against the official five-criterion rubric and returns, for every student:

- an **annotated manuscript PDF** — red highlights and criterion-tagged margin comments
  (`[CLO2 - Methodology]`, `[CLO3 - Ethics]`, …), authored by the evaluator;
- a **filled marking form PDF** — name, matric, evaluator and the five marks on the official
  form, followed by a summary page with CLO weighting, total /60, strengths and priority
  improvements;
- **result.json** — the validated model output, evidence, comments, computed marks, model,
  token usage and source hash (the audit record and regeneration input).

A batch output root also gets `gradebook.csv`, `review.html` and `batch-summary.json`.

The module is designed to be used directly from the command line or as a
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) skill, where the agent
can drive the workflow in conversation.

## What it handles

- English, Malay, and Indonesian proposals.
- Text-based PDFs, one manuscript per file.
- Automatic extraction of student name, matric number and title.
- Refusal of image-only scans, theses/dissertations, and documents without proposal
  sections — these are reported as failures rather than marked with nonsense.
- Resumable batches keyed by source hash; one failed file does not stop the run.
- Human review queue for low-confidence results, unmatched quotes, and missing identity.

## Requirements

- Python 3.11+ (tested on 3.14)
- A DeepSeek API key
- macOS or Linux. The form uses Arial when present and falls back to Helvetica.

## Install

```sh
git clone https://github.com/fathur6/rmc-assignment-marking.git
cd rmc-assignment-marking

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DEEPSEEK_API_KEY=sk-...
.venv/bin/python rmc.py doctor
```

`doctor` checks the dependencies, the marking-form template, the calibration file, the API
key and the reachable models. The key can also live in the DSH credential store
(`$DSH_HOME/.credentials.yaml`, `refs.DEEPSEEK_API_KEY`); the environment variable wins.

Use the launcher without installing anything globally:

```sh
export PATH="$PWD/bin:$PATH"
rmc doctor
```

Or call `.venv/bin/python rmc.py ...` directly.

## Quick start

```sh
# 1. Drop manuscripts into the configured inbox
mkdir -p ~/Documents/RMC-Marking/inbox
cp /path/to/manuscripts/*.pdf ~/Documents/RMC-Marking/inbox/

# 2. Mark them
./bin/rmc batch

# 3. Review
open ~/Documents/RMC-Marking/marked/review.html   # macOS; xdg-open on Linux
```

Default locations are configured in `config.yaml`:

| Setting | Default |
|---|---|
| `batch.inbox` | `~/Documents/RMC-Marking/inbox` |
| `batch.output_dir` | `~/Documents/RMC-Marking/marked` |
| `marking.evaluator` | `Fathurrahman Lananan` |
| `marking.model` | `deepseek-flash` |

Edit `config.yaml` for your own evaluator name, semester, paths, model, or concurrency.
`rmc mark FILE --evaluator "Name"` overrides the name for one run.

### Single manuscript

```sh
./bin/rmc mark "/path/to/manuscript.pdf" --dry-run    # scores only, writes nothing
./bin/rmc mark "/path/to/manuscript.pdf" --out ./marked
```

Always dry-run one script before a large batch: it prints the five marks, the total and any
review flags in roughly 45–70 seconds.

## Output layout

```
~/Documents/RMC-Marking/marked/
  SI1234 - Student Name/
    SI1234 - Student Name - Annotated assignment.pdf
    SI1234 - Student Name - Marking form.pdf
    result.json
  gradebook.csv
  review.html
  batch-summary.json
```

## Commands

| Command | Purpose |
|---|---|
| `rmc doctor` | Check dependencies, template, calibration, API key and models. |
| `rmc mark PDF...` | Mark one or more manuscripts. |
| `rmc batch [DIR...]` | Mark a folder recursively; with no arguments, uses the configured inbox. |
| `rmc finalize result.json...` | Confirm marks as final, stamp the form footer, update the gradebook. |
| `rmc regen result.json...` | Rebuild the PDFs from saved JSON without calling the model. |
| `rmc report DIR` | Rebuild `review.html` from `gradebook.csv`. |

Useful flags: `--evaluator "Name"`, `--model deepseek-v4-pro`, `--concurrency N`,
`--limit N`, `--overwrite`, `--dry-run`, `--json`.

## Rubric and marks

| Criterion | CLO | Raw | Weight |
|---|---|---|---|
| Organisation and coherence of proposal writing | CLO2 | 0–10 | part of 40% |
| Clarity and alignment of research components | CLO2 | 0–10 | part of 40% |
| Use of literature to support academic writing | CLO2 | 0–10 | part of 40% |
| Academic integrity and citation practice | CLO3 | 0–10 | part of 20% |
| Professional academic practice and ethical awareness | CLO3 | 0–10 | part of 20% |

CLO2 = subtotal / 30 × 40; CLO3 = subtotal / 20 × 20; assignment total /60. Bands: 0–1 very
weak, 2–3 weak, 4–5 satisfactory, 6–8 good, 9–10 excellent.

The prompt anchors each band at 1/3/5/6/9 to keep marks consistent across a batch, and
`prompts/calibration.json` holds a moderated reference: a complete but generic proposal
scores **6, 5, 5, 6, 5 = 32.33/60 (53.9%)**.

## Workflow for a large batch

1. `rmc doctor`
2. `rmc mark <one script> --dry-run` to sanity-check the marks.
3. `rmc batch` and let it run; stop and rerun at any time — finished scripts are skipped.
4. Open `review.html`. Flagged rows sort first; spot-check 5–10% of clean rows.
5. Fix individual scripts by editing `result.json` and running `rmc regen`, or by rerunning
   that one file with `rmc mark --overwrite`.
6. When the evaluator accepts the marks, run `rmc finalize <result.json...>`. The form footer
   then reads "Confirmed as final by … on …" and the dashboard row becomes FINAL.

Exit codes: `0` success, `1` when any file failed or was refused.

## Use as a DeepSeek Harness skill

The repository contains `SKILL.md`, so it can be installed as a DSH user skill:

```sh
git clone https://github.com/fathur6/rmc-assignment-marking.git \
  ~/.dsh/skills/rmc-assignment-marking
cd ~/.dsh/skills/rmc-assignment-marking
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The skill then appears in the DSH catalog and the agent can run `rmc` (or the bundled
`bin/rmc`) itself. Keep the API key in the environment or the DSH credential store.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests are keyless: they cover rubric arithmetic, text extraction and anchor location,
PDF annotation, form filling, validation, guards, and report generation. They do not call
the API.

## Cost and throughput

On `deepseek-flash`, one manuscript takes roughly 45–70 seconds and 15–20k tokens (including
reasoning tokens); four workers process about 200–250 scripts per hour. `deepseek-v4-pro` is
slower (~2.5 minutes) and more expensive; use it for moderation disputes rather than routine
batches. Check the DeepSeek balance before a large run.

## Data and privacy

Manuscripts, extracted text and results stay on the machine; only the page-tagged extracted
text and the rubric prompt are sent to the DeepSeek API. Output folders contain student
data — treat them like marked scripts. No API key or student data is stored in this
repository; the calibration example is anonymous.

## Limits

- Text-based PDFs only. Scanned pages produce little or no text and are refused; OCR them
  first or mark manually.
- The module judges the extracted text against the rubric. It does not verify references
  against external databases, and it does not run plagiarism detection.
- Marks are provisional until finalized; the model can vary by roughly one point on a
  criterion between runs, so keep the review step.
- The form template is fixed to the official RMC marking form; replacing it requires
  re-measuring the field coordinates in `engine/form.py`.
- macOS font paths are preferred; Helvetica is used when Arial is unavailable.

## Layout

```
SKILL.md              DSH skill instructions
config.yaml           evaluator, model, paths, batch settings
rmc.py                CLI entry point
bin/rmc               launcher using the bundled .venv
engine/               extraction, prompt/LLM, annotation, form, batch, report
prompts/              system prompt and calibration example
assets/               official marking form template
tests/                keyless unit tests
```
