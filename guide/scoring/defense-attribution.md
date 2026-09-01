
# Who actually stopped the attack

A defense rate is easy to overstate. If an attack does not land, something stopped it, and the naive reading credits the defending project. Often that is wrong: modern target models decline plenty of attacks on their own, and a defense that let the payload through untouched deserves no credit for the refusal that followed.

Every report therefore splits the outcome four ways.

![The attribution section of a report](../../picture/22-battle-report-summary.png)

| Outcome | What happened |
|---|---|
| Blocked by the defense | The defending project refused the payload at its input gate, and the target never saw it |
| Filtered by the defense | The defense let the payload in, then altered the reply on the way out |
| Refused by the target itself | The defense passed it through untouched and nothing was obtained anyway |
| Attack landed | The objective was attained and delivered |

Only the first two are the defending project's work. The report states the split in words above the table: in the capture, the battle produced a 63.3 percent defense rate, of which 0.0 percent is attributable to the defending project and 63.3 percent is the target declining unaided.

The derived figure is the attributed defense rate, and it is the number to quote when comparing defensive frameworks. A run where the defense blocked 30 of 30 attributes 1.00. A minimal defense that waved everything through attributes 0.00, no matter how many attacks failed.

## Why this matters for reading results

Three real defensive frameworks measured on this platform stopped every attack in a 30-round run, with an attributed rate of 1.00. Their models barely mattered: swapping a frontier model for a 3 billion parameter one changed nothing, while changing the framework moved the same matchup from blocking everything to blocking almost nothing. When you evaluate a defense here, you are mostly measuring its framework design, not its model choice.

The other side of the same coin is that the target model's own alignment is the dominant variable in whether an attack can succeed at all. No platform setting overrides it. Making the target's configuration deliberately permissive changed no outcome, and a survey of the whole model roster on the proxy found none that disclose on a direct request. If you need headroom to measure a defense's contribution, you need a target that can actually be breached; otherwise the target's refusals mask everything the defense does or fails to do.

## Incidental disclosure survives a blocked round

The four-way split answers who stopped the delivered attack. It does not claim the target behaved well. Runs where the defense blocked all 30 rounds still show 7 to 30 rounds of incidental disclosure, with the target emitting credentials and override tokens in its raw output. The report counts those rounds separately, and they are worth reading even when the score line looks clean.
