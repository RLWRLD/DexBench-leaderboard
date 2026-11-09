# DexBench Leaderboard

Submit and explore robot policy evaluation results for
[DexBench](https://github.com/RLWRLD/DexBench). This repository hosts submission
records, reviewed results, and the leaderboard website.

**Submissions are not open yet.** Accepted evaluator releases and data access will be
announced here. The [release registry](releases.json) is currently empty; templates
are examples, not benchmark results.

## Submission requirements

- Use the release's designated official training data and **one checkpoint and
  inference system across all tasks**. Additional DexBench demonstrations, synthetic
  training trajectories, and simulator training/RL are not accepted. Pretrained
  models are allowed with their provenance disclosed.
- Complete **every task, cell, and required row** in the accepted release. Preserve
  all successful and failed rollouts.
- Include complete trajectories: initial state, observations, executed and processed
  actions, post-step states, episode identity, and original evaluation results.
- Include the **left head/ego video for every episode**, successes and failures:
  `robot_pov_left_cam_rgb` (policy input `camera_ego_left`). Other cameras are optional.
- Provide a method card, model configuration, checkpoint SHA-256, and an accessible
  evidence bundle. A paper is optional.

Keep videos at their original paths relative to each run directory:

```text
arena/<Run>/robot-cam-rebuild<r>-env<e>-robot_pov_left_cam_rgb-episode-<n>.mp4
```

List every required video and its SHA-256 in `runs[].videos`. The evaluator checks
episode coverage, file hashes, and non-empty files; reviewers inspect the content.
See the [method-card template](templates/method_card.md) for the full disclosure list.

## Submit a result

Once submissions open:

1. Fork this repository, create a branch, and install Python 3.11+ dependencies:

   ```bash
   python -m pip install -r requirements.txt
   python scripts/leaderboard.py new my-team/my-model/revision-1
   ```

2. Evaluate with the accepted DexBench release and prepare the evidence bundle.
   Finalize its method card, model entry, and metadata hashes, then run that release's
   `scripts/evaluation/validate_leaderboard_submission.py validate` command against
   the official conditions and reference pins. Preserve the generated report.
3. Fill `entry.json` with the bundle's HTTPS URL and SHA-256. Copy the bundle's
   `submission.json`, `model_entry.json`, and `method_card.md` without changing their
   bytes, and include the original validation report as `validation.json`.
4. Validate locally and open a pull request adding these five files:

   ```bash
   python scripts/leaderboard.py validate
   ```

   ```text
   submissions/my-team/my-model/revision-1/
     entry.json
     submission.json
     model_entry.json
     method_card.md
     validation.json
   ```

Store large HDF5, MP4, checkpoint, and archive files in durable artifact storage.
The bundle must include the original run directories; its paths are relative to the
bundle. Keep credentials out of submissions. Use a new revision for corrections;
existing entries are immutable.

For an outside model, use the evaluator's `--model-entry` configuration and include
that exact object as `model_entry.json`. Official baselines use their key, such as
`{"key": "gr00t"}`. Maintainers register reviewed outside models before publication;
they appear in the same ranking with a **Submitted** label.

## Scores and review

Results are grouped by evaluator release and evaluation version (`v1` or `v1-plus`).
Each task score averages its required cells equally. **Overall** is the unweighted
mean of task scores across the complete suite.

PR checks validate metadata, release eligibility, coverage, hashes, and score
arithmetic. Maintainers audit the original evidence with the accepted offline
validator before publishing an entry. Reviews do not rerun the submitted policy.

- **Reported:** the submitter's results and eligibility have passed evidence review.
- **Withdrawn:** the entry is hidden from the board; its record and reason remain.

## Preview locally

```bash
python -m unittest discover -s tests -v
python scripts/leaderboard.py build --output site
python -m http.server 8000 --directory site
```

Open <http://localhost:8000>. The board supports model/team search, release and version
filters, task scores, sortable rankings, and evidence links. CI also produces a
`leaderboard-preview` artifact.

See [Maintainer operations](docs/MAINTAINERS.md) for release registration, evidence
review, repository protection, and website publishing.
