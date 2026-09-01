# Thinking streams

Click a side's screen, or an individual agent, and you get a chat-style transcript of what that role actually reasoned about. Every line is sourced from a backend event. If a role produced no text for a round, the stream shows nothing for that round rather than filling the gap.

![The red side thinking stream](../../picture/05-red-side-thinking-stream.png)

The tabs across the top switch between roles: the recon analyst, the strategy analyzer, the rewriter and the judge. The red stream is where you can watch an attacking project change its mind between rounds, which is the whole point of the inner loop.

![The blue side thinking stream](../../picture/09-blue-side-thinking-stream.png)

The blue stream reads the same way. A defender's line usually carries the decision and the reason for it, which is the text the report later quotes when it attributes a block to the defending project.

Two things worth knowing:

The streams are per session. Re-attach to an old battle from the sidebar and the per-session stream replays its history, so the transcripts come back with it.

Helper roles only speak when the inner loop is on. With both loops off you will see the fighters, the target, and the judge, and the helper tabs will be empty. That is not a bug, and it is worth checking before concluding that a run went wrong.
