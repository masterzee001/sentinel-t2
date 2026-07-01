# Master Sprint 13 - Early Failure Detection Engine

Generated: 2026-06-30T01:46:05.529395+00:00

## Safety
- Advisory-only: True
- Live auto-exit enabled: False
- Broker order modified: False
- Production rules modified: False
- Autonomous execution: False

## Results
- Original Elite: PF 2.84, WR 72.6%, Trades 151, DD 3.72%, AvgLoss -1.0
- EFDE Enhanced: PF 3.02, WR 72.45%, Trades 151, DD 3.37%, AvgLoss -0.7
- EFDE Accuracy: 100.0%
- False Exit Rate: 0.0%
- Saved Loss Value: 10.08
- Missed Winner Value: 0.0
- PF Delta: 0.18
- DD Delta: -0.35
- EFDE Learning Score: 100.0
- FPS Calibration Accuracy: 100.0
- Recommended Threshold: 75
- Calibration Confidence: HIGH

Decision: PASS
Recommendation: keep advisory only; future review threshold is FPS >= 75 after 30%-50% adverse movement.
