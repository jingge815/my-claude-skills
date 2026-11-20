---
name: arena
description: "Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape."
disable-model-invocation: true
---

# Arena

Open a todolist with one entry per phase (Frame, Fan out, Cross-judge, Pick, Graft, Verify) before launching anything.

Runners and the judge come from `~/.claude/skills/dev-flow/config.toml`: each `[[panel.arena]]` entry is one candidate (engine plus, for the claude engine, a settings file that selects the vendor); the judge is the `[leader]` role. Launch each external candidate with `python3 ~/.claude/skills/dev-flow/agents.py run arena:<name> --workdir <dir> --prompt-file <p.md>`; an entry with `in_session = true` runs as an Agent subagent in this session instead. Keep at least two runners; note dropouts and proceed.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric, 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task.
3. Pick the runners from the `[[panel.arena]]` entries (see above). Add entries for additional design directions; two is the floor.
4. Give each candidate its own output directory inside the task worktree, e.g. `docs/arena/<task>/candidate-<n>/`. One worktree per task, never one per candidate (the dev-flow worktree rules).

## Phase B: Fan out

Launch all N candidates in parallel — N background `agents.py` processes, or one message with N Agent calls for in-session seats — each with the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected.

If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

After all Phase B candidates complete, run one readonly judge on the `[leader]` role (`agents.py run leader ...`). When possible the judge runs on a different vendor than every candidate. While you read in Phase D, the judge scores each candidate, by path label, on each rubric criterion and recommends a base.

## Phase D: Pick a base

Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge when Phase C ran one. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API.

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand so the result stays coherent under one mental model. Don't paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape. No graft is needed. If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output: run the checks it claims to satisfy. If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Don't paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming the base and why, the cross-judge's verdict or that Phase C skipped the judge, each graft with its source candidate, what was rejected and why, any convergence or dropouts, and the verification result.
