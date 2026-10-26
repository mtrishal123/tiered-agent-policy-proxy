# Tiered Agent Policy Proxy

Runtime policy checks for AI agent tool calls that don't make every action wait on an LLM.

An agent's proposed tool call is checked before it runs. Most policy checks are
deterministic ("refunds ≤ $500", "email only customer addresses"), so they're
compiled into rules that run in microseconds. Only the parts that need judgment
("the customer actually reported a problem with this order") go to an LLM judge,
only in the band where they matter, and the verdict is cached against the exact
evidence the judge read.

```
tool call ──► T0  deterministic rules ──── block / allow          ~1 µs
                  │ a judge policy applies and its `when` band matches
                  ▼
              T1  cached verdict for (policy version, call, evidence)  µs
                  │ miss
                  ▼
              T2  LLM judge
                    mutate tools: decide before the action runs; timeout → on_failure
                    read tools:   allow now, judge in background, flag + cache the verdict
```

## Results

5,000 synthetic support-agent tool calls, same traffic and policies for all three modes
(`make bench`, full output in [results/bench.md](results/bench.md)):

| mode | p50 ms | p90 ms | p99 ms | mean ms | judge cost / 1k actions | violations prevented | false blocks |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLM judge on every call | 407 | 752 | 1263 | 455 | $5.50 | 97.6% | 2.66% |
| rules only | 0.001 | 0.001 | 0.001 | 0.001 | $0 | 67.7% | 0% |
| **tiered** | **0.001** | 154 | 792 | **48** | **$1.14** | 92.8% (+6.3% flagged after) | **0.55%** |

- **90% of calls are decided in under 5 ms.** Calls that never wait on a judge cost p50 1 µs / p99 8 µs of engine time (measured, in-process).
- **Judge spend falls ~79%** and mean latency ~90%, because about 10% of calls need a judgment on the request path.
- **Fewer false blocks.** Rules don't make judgment errors, and the judge sees a fifth of the traffic.
- **The tail is still the judge.** p99 is set by the share of calls that need a request-path judgment. Getting p99 under 10 ms needs that share below 1%, either by compiling more of each policy into rules or by a faster judge. That's the next problem, not a solved one.
- **One deliberate tradeoff.** Read-only calls are allowed immediately and judged in the background, so 62 bad reads were flagged after the fact instead of prevented (a repeat of the same read is then blocked from the cache). Mutations are always judged before they run.

**How the numbers were made.** Engine overhead is measured. Judge latency (log-normal, median 400 ms), accuracy (2% error) and cost ($0.0055 per call) are simulated assumptions, and the traffic mix is synthetic ([bench/dataset.py](bench/dataset.py)). Ground truth comes from a separate implementation of the policy spec, not from the engine. The tiered row varies by a few calls between runs, because background audits race with repeated calls.

## Run it

```bash
make setup     # venv + deps (Python 3.11+)
make demo      # scripted support-agent run, simulated judge, no API key
make bench     # the table above
make test
make demo-claude   # Claude as the tier-2 judge (needs ANTHROPIC_API_KEY)
```

The demo walks a support agent through a conversation. It shows a hard limit blocked with a hint the agent follows, a refund approved by the judge, a refund for an order the customer never mentioned blocked, internal notes caught in an email, a retried email served from the cache, a profile snoop flagged by the background audit and then blocked, and a destructive tool denied. The agent's steps are scripted so the run is repeatable; every decision, latency and feedback message is real engine output.

## Policies

Policies are YAML ([policies/support.yaml](policies/support.yaml)):

```yaml
- id: refund-limits
  tools: [refund]
  risk: mutate            # judged before the action runs
  on_failure: block       # if the judge times out or errors
  rules:                  # T0
    - {arg: amount, gt: 0}
    - {arg: amount, lte: 500, message: Refunds above $500 need manager approval.,
       hint: Call create_ticket with type=refund_approval instead.}
  judge:                  # T2, only when every `when` condition holds
    prompt: A refund over $100 is allowed only if the customer reported a problem with that order.
    when: [{arg: amount, gt: 100}]
    evidence: [customer_messages]   # what the judge reads; part of the cache key
```

Operators: `lt lte gt gte eq regex in not_in contains_any not_contains_any max_len present absent`. A missing argument fails unless the condition says `optional: true`. Regexes must match the whole value. Tools no policy covers are blocked (`unmatched: block`).

A blocked call returns structured feedback (`policy`, `reason`, `hint`) so the agent can change course instead of retrying blindly.

## Layout

```
policyproxy/   engine (tiers), policy loader, decision cache, evidence, judges, guard
policies/      support-agent policies
demo/          fake support backend, oracle, scripted demo
bench/         synthetic traffic with ground truth, benchmark runner
tests/
```

`ClaudeJudge` uses structured outputs for the verdict and server-side refusal fallbacks. It treats the tool call and evidence as untrusted data, and blocks when the evidence doesn't establish compliance.

## Limitations

- Synthetic traffic and a simulated judge. The live-judge path is tested against a stub client, not benchmarked.
- `when` gates (e.g. "escalate emails that mention refunds") only help as much as their recall on real traffic. A violation that slips past a gate is never judged.
- The cache keys on exact arguments. Near-duplicate calls miss.
- The engine runs in-process. A network proxy adds a hop the numbers don't include.
- The demo agent is scripted, not an LLM.

## Next

- Drive the demo with a real agent loop (LangGraph / MCP) and measure live judge latency.
- Generate edge-case calls per policy and diff the compiled rules against an independent judge, so plain-English policies are tested before they ship.
- Run as an MCP proxy in front of existing tool servers.

MIT licensed.
