# Registered models

Maintainers add `<key>.json` using `scripts/leaderboard.py register` after auditing a
submission. Entries bind a community model config to one checkpoint hash and label.
Existing keys are immutable; a new checkpoint or config needs a new key. A participant
cannot promote a model by naming its group `registered`. See [operations](../docs/MAINTAINERS.md).
