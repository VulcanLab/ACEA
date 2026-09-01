# Reporting a vulnerability or a bug

This project runs adversarial exercises against AI systems. It deliberately generates attack payloads and stores the results, so some of what it does looks like, and in the wrong environment could become, real attacker behaviour. Please read the operating assumptions below before deploying it anywhere that matters.

## What to report, and where

| Kind | Where | What to include |
|---|---|---|
| **Security vulnerability**: sandbox escape, unintended write outside a work copy, credential exposure, an unauthenticated path that changes state | Open a **private** report to the maintainers rather than a public issue | Version/commit, configuration, the exact request or scenario, what you expected, what happened |
| **Ordinary bug**: a wrong score, a stuck battle, a report that does not match the recorded data, a UI fault | A normal issue | Session id if there is one, the round, the relevant log lines, and how to reproduce |
| **Evaluation defect**: the judge scores something that should not count, or misses something that should | A normal issue, labelled as evaluation | The scenario you declared, the attack, the target's output, and the score you expected instead |

Please do not include real personal data, real credentials, or content from a system you do not own. Reduce a report to the smallest case that still shows the problem.

No CVE is assigned to anything reported here. This is a research platform without a
published-release cadence a CVE identifier would track, not a claim that a report
lacks merit; a real finding gets fixed regardless.

## Contributing

Bug reports, vulnerability reports, and pull requests are all welcome, through the
routes above and through the usual pull-request flow. There is no separate
contributor agreement to sign; the license in [LICENSE](LICENSE) is what governs a
contribution.

## Operating assumptions

The platform is built for a controlled research environment. It assumes:

- **You own, or are authorised to test, every system you connect.** The attacker side exists to try to make the target misbehave. Pointing it at a third-party service you do not control is not a supported use.
- **The target is a sandbox, not production.** Its data is seeded fixtures. Its actions operate on an in-process ledger that resets with the service; nothing leaves the process.
- **Model safety filters may be relaxed for the target.** An engagement measures whether *your* defence holds, so a vendor filter silently refusing on your behalf would corrupt the measurement. That makes the target deliberately easier to push than a production system; do not read its behaviour as a safety evaluation of the underlying model.
- **Generated attack text is stored.** Traces and reports contain payloads written to provoke a system. Treat the reports directory accordingly.
- **Generated attack payloads are model-written text, executed against the target only.** The platform does not write or run code on your behalf; a payload is a string sent over the protocol, and the target's own sandboxing is what limits its effect.

## What is deliberately out of scope

- Making the platform safe to point at systems you do not control.
- Preventing an operator from configuring an engagement that is itself unethical.
- Hardening the bundled sample projects; they exist to exercise the protocol, not to be deployed.

## Known limitations

These are documented rather than hidden, because a measurement platform that overstates its own reliability is worse than one that does not measure at all. The current set is listed in the README under **Known limitations**, and the paper's limitations section gives the longer form with the evidence behind each one.
