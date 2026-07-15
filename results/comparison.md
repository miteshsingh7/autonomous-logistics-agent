# Model Comparison: Autonomous Rerouting Agent

Trajectory-based evaluation. Each dimension is normalised to [0, 1]; `Overall` is a weighted composite (tool 0.30, reasoning 0.30, success 0.25, recovery 0.15 when applicable).

## Aggregate (mean across scenarios)

| Model   |   Tool |   Reason |   Recover |   Success |   Overall |
|---------|--------|----------|-----------|-----------|-----------|
| closed  |      1 |     0.94 |         1 |         1 |      0.98 |
| open    |      1 |     0.74 |         1 |         1 |      0.91 |

## Per-scenario detail

| Scenario              | Model   |   Tool |   Reason | Recover   |   Success |   Overall |
|-----------------------|---------|--------|----------|-----------|-----------|-----------|
| S1-weather-delay      | closed  |      1 |      0.9 | -         |         1 |      0.96 |
| S2-breakdown          | closed  |      1 |      0.9 | -         |         1 |      0.96 |
| S3-highvalue-air      | closed  |      1 |      0.9 | -         |         1 |      0.96 |
| S4-fault-alternatives | closed  |      1 |      1   | 1.00      |         1 |      1    |
| S5-fault-telemetry    | closed  |      1 |      1   | 1.00      |         1 |      1    |
| S1-weather-delay      | open    |      1 |      0.7 | -         |         1 |      0.89 |
| S2-breakdown          | open    |      1 |      0.7 | -         |         1 |      0.89 |
| S3-highvalue-air      | open    |      1 |      0.7 | -         |         1 |      0.89 |
| S4-fault-alternatives | open    |      1 |      0.8 | 1.00      |         1 |      0.94 |
| S5-fault-telemetry    | open    |      1 |      0.8 | 1.00      |         1 |      0.94 |
