# Benchmark: 5000 synthetic support-agent tool calls (seed 7)

Traffic: {'ok': 4019, 'rule': 664, 'semantic': 237, 'semantic-read': 80}; 981 calls should be blocked.
Judge: simulated, median 400 ms, 2% error rate, 1500 ms timeout, $0.0055 per call (assumed).

| mode | p50 ms | p90 ms | p99 ms | mean ms | judge calls / 1k (request path) | judge cost / 1k actions | prevented | detected after | missed | false blocks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| llm-every-call | 407 | 752 | 1263 | 455 | 1000 (1000) | $5.50 | 957/981 (97.6%) | 0 | 24 | 107/4019 (2.66%) |
| rules-only | 0.001 | 0.001 | 0.001 | 0.001 | 0 (0) | $0.00 | 664/981 (67.7%) | 0 | 317 | 0/4019 (0.00%) |
| tiered | 0.001 | 154 | 792 | 48 | 208 (103) | $1.14 | 910/981 (92.8%) | 62 | 9 | 22/4019 (0.55%) |

Tiered, by deciding tier: {'T0': 3564, 'T1': 398, 'T2': 508, 'T2-async': 525, 'T2-failed': 5}
Tiered, calls decided in under 5 ms: 89.7%
Tiered, measured engine overhead on calls that never wait for a judge (T0/T1/T2-async, n=4487): p50 1 µs, p99 8 µs

`detected after` = read-only calls allowed immediately and flagged by the background judge.
`judge calls / 1k` counts background audits; the number in parentheses is request-path calls only.
Engine overhead (T0/T1) is measured; judge latency, accuracy and cost are simulated assumptions.
