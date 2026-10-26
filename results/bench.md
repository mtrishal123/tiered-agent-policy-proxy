# Benchmark: 5000 synthetic support-agent tool calls (seed 7)

Traffic: {'ok': 3914, 'rule': 684, 'semantic': 317, 'semantic-read': 85}; 1086 calls should be blocked.
Judge: simulated, median 400 ms, 2% error rate, 1500 ms timeout, $0.0055 per call (assumed).

| mode | p50 ms | p90 ms | p99 ms | mean ms | judge calls / 1k (request path) | judge cost / 1k actions | prevented | detected after | missed | false blocks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| llm-every-call | 406 | 753 | 1303 | 457 | 1000 (1000) | $5.50 | 1060/1086 (97.6%) | 0 | 26 | 137/3914 (3.50%) |
| rules-only | 0.001 | 0.002 | 0.002 | 0.001 | 0 (0) | $0.00 | 684/1086 (63.0%) | 0 | 402 | 0/3914 (0.00%) |
| tiered | 0.001 | 213 | 823 | 52 | 213 (109) | $1.17 | 1009/1086 (92.9%) | 67 | 10 | 27/3914 (0.69%) |

Tiered, by deciding tier: {'T0': 3492, 'T1': 441, 'T2': 541, 'T2-async': 521, 'T2-failed': 5}
Tiered, calls decided in under 5 ms: 89.1%
Tiered, measured engine overhead on calls that never wait for a judge (T0/T1/T2-async, n=4454): p50 1 µs, p99 10 µs

`detected after` = read-only calls allowed immediately and flagged by the background judge.
`judge calls / 1k` counts background audits; the number in parentheses is request-path calls only.
Engine overhead (T0/T1) is measured; judge latency, accuracy and cost are simulated assumptions.
