# Maintainer operations

## Register an accepted release

`releases.json` is maintained by the evaluator owners. No release is enabled by the
initial implementation because production evaluation reference pins are not available.
Never fill this registry with template hashes or synthetic test fixtures.

1. Select an immutable evaluator commit and image. Its validator must contain the
   submission trajectory contract and per-task reference pins (version 2 or 3).
2. Generate reference candidates from trusted complete v1 or v1-plus evaluation runs with
   `validate_leaderboard_submission.py freeze-reference`; audit and commit each task's
   `benchmark/frozen/settings_db_<task>.json` in the accepted evaluator release.
3. Use `templates/release.example.json` as the registry format, replacing every value.
   Register the **entire task suite**, not just the first submission's tasks. Set each
   task's frozen/reference file hashes, evaluation-contract hashes, required cell names, and exact trial counts for
   v1 and v1-plus from the official manifest/version-row pins. Register the approved
   baseline model keys, immutable official training dataset URIs/revisions and supported
   `eval_versions` (v1-only pins cannot open v1-plus). Record the evaluator repository, full commit, full manifest
   hash and immutable evaluator image digest.
4. Add the release in a maintainer PR. Run validation and preserve existing releases;
   changing a release invalidates records made against the old values. Changes that
   change evaluation contracts, frozen sets or data eligibility need a new release ID. Reference pins may
   be reused when the evaluator's task-specific contract permits it.

The complete source manifest hash is provenance, not an equality gate against the release.
The evaluator's reference validity is based on task evaluation fields plus frozen-set
hash, excluding model/sensitivity metadata; this repo pins the approved reference
file bytes and does not reimplement its generation algorithm.

## Review a submission

The trusted `submission-contract` check reads the PR's Git blobs as data using the base
branch's validator, schemas and release registry. It allows only additions of complete
entries. It never checks out or runs PR code. The ordinary `tests-and-build` job also
runs tests and makes a preview with a read-only token; its output alone is not approval.

After CI passes, check training-data eligibility and all declared methods, access the
immutable bundle, verify its archive SHA-256, compare its metadata/method card with the
PR, and run the accepted offline Arena validator on the original evidence. Require
`robot_pov_left_cam_rgb` (`camera_ego_left`) video for every successful and failed
episode, with the original Run/rebuild/environment/episode identity and a non-empty,
hashed file; additional cameras are optional. Review video content, checkpoint
provenance, retries and reproducibility disclosures. The report's arithmetic
and hashes cannot prove that uploaded logs are authentic. Do not execute uploaded commands in CI.

After accepting an outside-model submission, create its registration candidate:

```bash
python scripts/leaderboard.py register my-team/my-model/revision-1 \
  --output models/MODEL_KEY.json
```

Use the exact model key as the filename. The registry binds the submitted config to
its checkpoint SHA-256 and recorded label. Keep existing keys immutable; a new config
or checkpoint gets a new key. Baselines named by the release need no separate registry
entry. The trusted PR gate refuses modifications or deletions of registered keys.

Create a **separate maintainer PR** for the registration and review:

```bash
python scripts/leaderboard.py review my-team/my-model/revision-1 \
  --status reported --reviewer YOUR_GITHUB_LOGIN \
  --evidence-url https://github.com/RLWRLD/DexBench-leaderboard/pull/123 \
  --notes 'Describe the artifact audit and eligibility decision' \
  --output reviews/my-team/my-model/revision-1/review.json
python scripts/leaderboard.py validate
```

Replace the example PR number and all placeholders. The command produces a review
candidate bound to the exact five submission files. It does not grant authority to
its caller; required maintainer approval on the PR is the authorization boundary.

## Corrections and withdrawals

Reviews audit the submitted artifacts without executing the policy. Published scores
always come from the submitted run. The Arena report's `verification` field describes
artifact validation only; older reports with `not independently rerun` are accepted
unchanged. A report never substitutes for the maintainer's review decision.

For an existing review, write the candidate to a new temporary path, review the diff,
then replace its `review.json` in a maintainer PR. Git preserves the earlier decision.
Use `--status withdrawn` and an explanation to hide a result. Corrected submissions
get a new revision; never edit or delete the old submission files.

## Repository setup

- Keep this repository independent of source exports. Do not target it with the Arena
  export/sync tool. Accepted submission records exist only here.
- Configure the default branch to require PRs, code-owner approval and both
  `tests-and-build` and `submission-contract` checks. Disallow force pushes/deletions;
  dismiss stale approvals after changes. `.github/CODEOWNERS` initially names
  `@mydulee06`; change that to the actual maintainer team when it exists.
- Infrastructure/release/review-only PRs pass the submission gate without claiming to
  be submissions. They require maintainer review. Do not mix them with entry additions.
- Keep Actions tokens read-only by default. Workflows are pinned to action commits.
  The PR-target workflow fetches but never executes head code. No secrets or policy
  servers are needed for metadata checks.
- Public external forks require a public repository. Visibility is an owner setting;
  this implementation does not change it. Keep artifact retention/access requirements
  in the accepted release announcement.
- To publish a website, enable GitHub Pages with **GitHub Actions** as the source,
  restrict the `github-pages` environment to the default branch, and manually run
  **Publish leaderboard** on that branch. The workflow tests and rebuilds before
  deployment. Ordinary PR/push checks only upload preview artifacts. A private repo
  may require a qualifying plan for Pages and protection features.

Before opening submissions, the evaluator release maintainer produces and reviews pins
for every task and cell. Existing complete v1 runs can supply v1-only pins; remaining
v1-plus rows need their own trusted evidence. Do not open a release with missing pins.
