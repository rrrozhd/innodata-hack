# Pilot / evaluation results

_Generated 2026-11-14T14:59:59 by `python run_tests.py`. Paste or adapt this into the README's 'Pilot results' section._

| model | correct / valid | accuracy | invalid | cost (USD) |
|---|---|---|---|---|
| Claude Haiku 5.5 | 10/10 | 100% | 0 | 0.0047 |
| GPT-6 Luna | 9/10 | 90% | 0 | 0.0026 |

| task | Claude Haiku 5.5 | GPT-6 Luna | verdict |
|---|---|---|---|
| ecg_beat_count_000 | ok | ok | both_pass |
| ecg_ectopic_count_000 | ok | ok | both_pass |
| ecg_interval_000 | ok | wrong | split |
| ecg_premature_beat_000 | ok | ok | both_pass |
| ecg_rate_000 | ok | ok | both_pass |
| ecg_beat_count_001 | ok | ok | both_pass |
| ecg_ectopic_count_001 | ok | ok | both_pass |
| ecg_interval_001 | ok | ok | both_pass |
| ecg_premature_beat_001 | ok | ok | both_pass |
| ecg_rate_001 | ok | ok | both_pass |
