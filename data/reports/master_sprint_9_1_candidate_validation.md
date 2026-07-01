# Master Sprint 9.1 - Controlled Candidate Validation

Generated: 2026-07-01T17:44:06.850123+00:00

## Safety
- Advisory only: True
- Production policy changed: False
- Live config changed: False
- Broker execution: False
- Autonomous execution: False

## Candidates
### Candidate 1 - Conditional Symbol Lock Relaxation
- 30D: PF 2.91, WR 72.65%, Trades 34, DD 1.0%, AvgRR 1.5, Tail MEDIUM
- 90D: PF 2.91, WR 72.65%, Trades 86, DD 2.1%, AvgRR 1.5, Tail MEDIUM
- 365D: PF 2.91, WR 72.65%, Trades 156, DD 3.81%, AvgRR 1.5, Tail MEDIUM
- Stress: FAIL PF_BELOW_2_9,WR_BELOW_73
- PF/DD efficiency: 0.778
- Decision: REJECTED

### Candidate 2 - Institutional Continuation No-Trade Relaxation
- 30D: PF 2.89, WR 72.6%, Trades 34, DD 1.0%, AvgRR 1.3, Tail MEDIUM_HIGH
- 90D: PF 2.89, WR 72.6%, Trades 84, DD 2.07%, AvgRR 1.3, Tail MEDIUM_HIGH
- 365D: PF 2.89, WR 72.6%, Trades 153, DD 3.76%, AvgRR 1.3, Tail MEDIUM_HIGH
- Stress: FAIL PF_BELOW_2_9,WR_BELOW_73
- PF/DD efficiency: 1.25
- Decision: REJECTED

### Candidate 3 - A+ Override Layer
- 30D: PF 2.95, WR 73.27%, Trades 35, DD 1.0%, AvgRR 1.47, Tail LOW_MEDIUM
- 90D: PF 2.94, WR 73.26%, Trades 87, DD 2.11%, AvgRR 1.47, Tail LOW_MEDIUM
- 365D: PF 2.94, WR 73.25%, Trades 158, DD 3.84%, AvgRR 1.47, Tail LOW_MEDIUM
- Stress: PASS 
- PF/DD efficiency: 0.833
- Decision: APPROVED_FOR_FUTURE_REVIEW

### Candidate 4 - Combined Controlled Relaxation
- 30D: PF 2.94, WR 73.1%, Trades 35, DD 1.0%, AvgRR 1.43, Tail HIGH
- 90D: PF 2.93, WR 73.1%, Trades 88, DD 2.13%, AvgRR 1.43, Tail HIGH
- 365D: PF 2.93, WR 73.09%, Trades 160, DD 3.88%, AvgRR 1.43, Tail HIGH
- Stress: FAIL PF_BELOW_2_9,WR_BELOW_73,DD_GTE_4
- PF/DD efficiency: 0.563
- Decision: REJECTED

## Ranking
1. Candidate 3 - A+ Override Layer - APPROVED_FOR_FUTURE_REVIEW - efficiency 0.833
2. Candidate 2 - Institutional Continuation No-Trade Relaxation - REJECTED - efficiency 1.25
3. Candidate 4 - Combined Controlled Relaxation - REJECTED - efficiency 0.563
4. Candidate 1 - Conditional Symbol Lock Relaxation - REJECTED - efficiency 0.778

Best Candidate: Candidate 3 - A+ Override Layer
Reason: candidate_3 is the only candidate classified APPROVED_FOR_FUTURE_REVIEW with stress_pass=True; PF/DD efficiency=0.833, PF=2.94, DD=3.84%.
Decision: PASS
