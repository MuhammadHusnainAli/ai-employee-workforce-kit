"""The agent step loop, built on agent-harness-adk's public API (ADR-0016).

One module per stage, each with serialisable inputs and outputs so Phase 2 can run it as a
Temporal activity:

    context.py   build_context   system prompt, compaction, tool manifest
    model.py     call_model      one model call with a budget pre-check
    validate.py  decide          tool-call validation, repair, guardrails, loop and approval gates
    act.py       act             run tools: reads in parallel, writes in order, idempotency keys
    observe.py   observe         pure: fold a step's results into the next AgentState
    loop.py      step / run      drive the stages one step at a time
"""
