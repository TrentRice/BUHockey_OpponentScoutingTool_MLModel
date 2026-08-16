# Architecture

High-level system design for this project. Replace the diagram and notes below with
the specifics of your project.

## Overview

```
Data source(s)
      │
      ▼
data/raw → data/interim → data/processed
      │
      ▼
Feature engineering
      │
      ▼
Model training  ──────────►  Experiment tracking (MLflow)
      │                              │
      ▼                              ▼
Model evaluation              Model registry
                                      │
                                      ▼
                          Serving app (FastAPI, Docker)
                                      │
                                      ▼
                   CI/CD (GitHub Actions) → ECR → ECS Fargate
                                      │
                                      ▼
                          Monitoring (drift / performance over time)
```

## Decisions log

Use this section as a lightweight ADR (Architecture Decision Record) log — one entry
per meaningful decision, so future-you (or a reviewer) understands *why*, not just
*what*.

### YYYY-MM-DD: Chose X over Y

**Context:** What problem were you solving?
**Decision:** What did you choose?
**Why:** What made this the right tradeoff at the time?
**Revisit if:** What would make you reconsider this later?

---

Add new entries above this line, most recent first.
