# Release/current interface review

Released source: `4f2e5c6715c50ca86038a1e868dc9437b7e30c3a` (`v0.4.0`).
Current public main before notebook edits: `0883a87b219a0670fcfdcb4f153deb93909f574a`.
Both complete actual runtime exports contain the same 14 named SQL surfaces and signatures.

`git diff v0.4.0 0883a87 -- vgi_ortools` is retained in `release-to-current.diff`.
Only catalog metadata and flow example/doc declarations change. The solver models,
argument marshaling, processing methods, status semantics, and root dependency
constraints are unchanged. The new shipping story uses the composed network added
to current metadata, but all calls execute the published v0.4.0 implementation.
The named-surface mappings apply to both interfaces; their actual runtime receipts
identify the released commit and do not claim that current-main code executed.
