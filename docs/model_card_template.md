# Model Card: <model name>

Copy this template once per model you register/promote. Fill in every section —
"N/A" is a valid answer, blank is not.

## Summary

- **Model type:** (e.g. gradient-boosted classifier)
- **Version / MLflow run ID:**
- **Date trained:**
- **Intended use:**
- **Out-of-scope uses:** (be explicit about what this model should *not* be used for)

## Training data

- **Source(s):**
- **Time range covered:**
- **Size:** (rows, unique entities, etc.)
- **Known gaps or biases:**

## Features

- Brief list or link to `src/features/` module used
- Any features deliberately excluded, and why

## Evaluation

- **Metric(s) used and why:** (e.g. Brier score + calibration, not just accuracy)
- **Validation strategy:** (e.g. time-based split — state this explicitly for any
  time-series data)
- **Headline results:**
- **Comparison against baseline:**

## Known limitations

- Conditions under which this model is expected to perform poorly
- Any fairness/bias considerations relevant to this project

## Monitoring

- What's being monitored in production (drift, calibration, etc.)
- Threshold that would trigger retraining or rollback

## Changelog

| Date | Change |
|---|---|
| YYYY-MM-DD | Initial version |
