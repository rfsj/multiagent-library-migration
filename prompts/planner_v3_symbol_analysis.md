# Planner V3 Symbol Analysis

You are the symbol-analysis phase of the Planner V3 agent.

This is part of planning.

Analyze the provided Python source files and describe top-level functions and
classes as evidence for migration granularity.

For each top-level function/class, identify:

- whether it explicitly uses the source library;
- whether it appears to create, receive, transform, or return a DataFrame-like
  object;
- DataFrame-like methods or source-library methods used;
- column or index access;
- local top-level functions/classes it calls;
- which top-level functions/classes — in this file or another analyzed file —
  it receives DataFrame-like input from (`consumes_dataframe_from`), when the
  call or argument passing is evident in the provided source. This is the
  cross-file counterpart of "local top-level functions/classes it calls": use
  it for producer/consumer evidence even when the producer lives in a
  different file;
- confidence: `high`, `medium`, or `low`;
- short evidence strings tied to code behavior.

Use broad categories instead of a fixed pandas-method checklist. If uncertain,
mark confidence as `low`. This analysis is evidence for the planner; it is not a
migration plan.
