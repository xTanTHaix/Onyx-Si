# Reproducible MCTS demo

These two files are the exact workspace used for the **Live MCP Transcript** in
the top-level `README.md`. They exist so the transcript can be re-verified
instead of taken on faith.

- `calc.py` — starts with a bug: `add()` subtracts.
- `check.py` — the check that distinguishes a real fix from a cosmetic one.

## Reproduce it

```bash
# 1. open a ghost session and open two competing branches
ghost_sandbox cmd=begin
ghost_sandbox cmd=fork branch_id=branch_fast hypothesis="rename the helper, keep the body"
ghost_sandbox cmd=mutate path=demo_mcts/calc.py content="def add(a, b):\n    return a - b  # renamed, still wrong\n"
ghost_sandbox cmd=fork branch_id=branch_robust from_branch=main hypothesis="fix the operator, guard the type"
ghost_sandbox cmd=mutate path=demo_mcts/calc.py content="def add(a, b):\n    if not isinstance(a, (int, float)):\n        raise TypeError('a must be a number')\n    return a + b\n"

# 2. test each branch in an isolated shadow copy of the workspace
ghost_sandbox cmd=switch_branch branch_id=branch_fast
ghost_sandbox cmd=test command="python demo_mcts/check.py" timeout_s=60   # -> FAILED
ghost_sandbox cmd=switch_branch branch_id=branch_robust
ghost_sandbox cmd=test command="python demo_mcts/check.py" timeout_s=60   # -> PASSED

# 3. score, merge the winner, commit it
ghost_sandbox cmd=merge_best scores='[{"branch_id":"main","passed_tests":0,"total_tests":1},
                                       {"branch_id":"branch_fast","passed_tests":0,"total_tests":1},
                                       {"branch_id":"branch_robust","passed_tests":1,"total_tests":1}]'
ghost_sandbox cmd=commit

# 4. write the decision trail
wiki_task_sync task_id=TASK-001 op=replay project_name=OnyxSi
```

After step 3, `demo_mcts/calc.py` on disk contains the guarded addition, and
`.onyxsi/wiki/projects/<project>/tasks/TASK-001/TIME_TRAVEL_REPLAY.md` holds
the decision tree.
