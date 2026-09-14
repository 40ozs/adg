# Architecture decision records

One file per decision, named `NNNN-short-title.md` with a zero-padded sequence number.
Copy `adr-template.md` to start a new one.

Rules:

- A record is never edited to say something different after it is accepted. To change a
  decision, add a new ADR and set the old one's status to `Superseded by ADR-NNNN`.
- Record the decision and its consequences, not a tutorial.
- Link the ADR from the architecture document it constrains.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-sid-as-identity.md) | The SID is the canonical principal identity | Accepted |
| [0002](0002-graph-preserving-membership.md) | Membership is stored as a graph of edges | Accepted |
| [0003](0003-raw-observations-vs-derived-state.md) | Raw observations and derived state are stored separately | Accepted |
| [0004](0004-read-only-collector-posture.md) | Collectors are read-only and least-privileged | Accepted |
