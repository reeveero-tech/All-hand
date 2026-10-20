# R-169 v1 — change log of cases, references, scorer and prompts

Every change made after the first model run is listed here with the split
whose outputs motivated it. Only dev (and, for confirmation, validation)
outputs may motivate a change; the held-out test split is measured once, with
the configuration frozen in `FREEZE.json`, and nothing is changed after it.

| # | When | What changed | Motivated by | Effect on cases / references |
|---|---|---|---|---|
| 0 | before any run | dataset v1 generated (142 cases, seed 169), scorer and candidate prompt `grounded-v2` written from the task definitions | — | — |
| 1 | after dev baseline run `t1` | scorer: marker matching ignores Arabic diacritics/tatweel and typographic apostrophes (`prep_markers`) — «لا يُذكر» and «isn’t available» were scored as non-abstentions | dev `abstain_missing-00-ar`, `adversarial_user-00-en` | none (cases and reference answers unchanged) |
| 2 | after dev baseline run `t1` | scorer: `normalize` drops LaTeX thin spaces between digits (`299\,792\,458`) | dev `closed_book-00-en` | none |
| 3 | after dev `grounded-v2` run `t1` (manual reading of dev replies) | scorer: secondary metric `answer_language_match` (not part of `pass`) | dev `grounded_qa-00-en`, `stale_sources-00-en`, `prompt_injection-00-en` answered in German; several Arabic questions answered in English | none |
| 4 | same | candidate prompt `grounded-v2`: the last rule now names the reply language explicitly (Arabic for Arabic questions, English for English, never another language) | same dev cases | none; dev `grounded-v2` re-run as `t2` |
| 5 | after dev `grounded-v2` run `t2` | candidate prompt: after `INSUFFICIENT_EVIDENCE` the explanation is written in the question's language | dev `abstain_missing-00-ar`, `abstain_missing-05-ar`, `false_premise-00-ar`, `adversarial_user-00-ar` explained in English | none; validation `grounded-v2` runs with this prompt; dev re-run as `t3` to confirm |
