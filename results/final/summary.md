# Evaluation summary

Tasks: 10. Total cost: $0.12.

| Model | Correct | Wrong | Wrong answers matching a fingerprint | Expected by chance |
|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` | 0/10 (0%) | 10 | 2 of 10 (20%) | 6% |
| `openai/gpt-6-luna` | 0/10 (0%) | 10 | 4 of 10 (40%) | 6% |

## `anthropic/claude-haiku-5.5`

| Question type | Correct |
|---|---|
| tg_longest | 0/2 (0%) |
| tg_meets | 0/2 (0%) |
| tg_nth | 0/2 (0%) |
| tg_overtakes | 0/2 (0%) |
| tg_section | 0/2 (0%) |

| Level | Correct |
|---|---|
| 5 | 0/10 (0%) |

| Wrong answer consistent with | Count |
|---|---|
| unmatched | 8 |
| slip:missed_last | 1 |
| up_trains_only | 1 |

## `openai/gpt-6-luna`

| Question type | Correct |
|---|---|
| tg_longest | 0/2 (0%) |
| tg_meets | 0/2 (0%) |
| tg_nth | 0/2 (0%) |
| tg_overtakes | 0/2 (0%) |
| tg_section | 0/2 (0%) |

| Level | Correct |
|---|---|
| 5 | 0/10 (0%) |

| Wrong answer consistent with | Count |
|---|---|
| unmatched | 6 |
| ordered_by_departure | 2 |
| slip:missed_last | 1 |
| up_trains_only | 1 |

A fingerprint match means the wrong answer equals what that shortcut produces on the task. It is consistent with the shortcut, not proof that the model used it.
