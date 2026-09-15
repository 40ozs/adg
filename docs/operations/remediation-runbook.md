# Carrying out an ADG change plan

**Audience:** the Windows administrator who has been handed a signed change plan, and the
operator who configures ADG to produce one.

ADG has not made any of the changes in the document you are holding, and cannot make them. It
is an instruction. Nothing happens until you run it.

---

## 1. Before you run anything: verify the signature

The document is signed with HMAC-SHA256 under this deployment's key. Verifying proves it came
from your ADG deployment and has not been edited on the way to you — which is the only thing it
proves. It does **not** prove that a named approver pressed a button; that is recorded inside
the document and in the audit chain.

The recipe does not require ADG to be running:

```python
import hashlib, hmac, json, sys

document = json.load(open(sys.argv[1]))          # the "document" object from the export
signature = sys.argv[2]                          # the "signature" field beside it
key = sys.argv[3]                                # ADG_REMEDIATION_SIGNING_KEY

canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
expected = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
print("VALID" if hmac.compare_digest(expected, signature) else "INVALID")
```

Three things to check by eye as well:

* `kind` is `adg.remediation.change-plan`. A verifier that accepts any `kind` will happily
  check a signature over a document whose meaning it does not know.
* `signature_key_id` matches the key you are verifying with. It is a digest prefix of the key,
  so a mismatch means *"signed with a key I do not have"* rather than tampering — usually a
  rotation you have not caught up with.
* `execution.performed_by_adg` is `false`. It always is; the field exists so that this document
  cannot be mistaken for a record of work completed.

## 2. Read the impact before you read the steps

`impact` is the blast radius ADG measured with its production access engine. The number that
matters is not how many entries are removed — it is how many principals **lose access**, which
is often far fewer, because people usually hold access through more than one route.

If `impact` says a removal changes nothing, that is a real finding: the change achieves
nothing, and somebody should know before the change window rather than after it.

## 3. Read the preconditions

`preconditions` records what ADG last observed for every step. Every one should say
`satisfied`: a plan cannot be exported otherwise. The document carries them so that you can see
*when* ADG last looked — `last_observed_at` on each step — and decide whether that is recent
enough for the folder in question.

`basis_moved: true` means a collector has written something since the plan was written. It is
not a problem by itself; it means a scan ran, and none of what it found touched this plan.

## 4. Run the runbook

The script does nothing without `-Execute`:

```powershell
# Dry run first. Always.
pwsh -NoProfile -File .\plan-<id>.ps1

# Then, in the change window:
pwsh -NoProfile -File .\plan-<id>.ps1 -Execute
```

Each step re-reads the live object and compares it with what ADG observed. **If a step stops
the run, stop.** The message names the mismatch. It means the entry in front of you is not the
entry that was reviewed and approved, and the remaining steps were written against a world that
no longer exists. Ask for a plan built on current state rather than editing the script.

Two step kinds need you to be in the right place:

* **A local group.** The script says which computer. A BUILTIN SID names a different group on
  every machine, so running it on a domain controller edits the wrong group or none.
* **A directory group.** Run it where the `ActiveDirectory` module is available.

An inherited NTFS entry carries a warning instead of a command. It cannot be removed on the
folder that shows it: either remove it from the ancestor the warning names — which affects every
child — or break inheritance there first. The plan proposed neither, so neither is scripted.

## 5. Afterwards

Record the `document_digest`, the `audit.head_digest` and the `audit.head_index` in your change
ticket.

The head is the plan's audit chain **immediately before** the export event that produced your
document — necessarily so, because that event carries the document's digest and so cannot also
be inside it. To verify later, read the chain (`GET /api/v1/remediation/{id}/audit`), take the
event at `head_index`, and check its digest equals `head_digest`. Comparing against whatever
the head happens to be *now* will differ by at least one event and is not the check.

A chain that has been rewritten wholesale still verifies internally and will not match a digest
recorded outside the database, which is the whole reason to write it down.

**ADG will not know you did this until the next collection.** Run a scan of the affected server
and confirm the entries are gone. Comparing the plan to what the scan finds is, today, something
you do by eye — see `docs/architecture/remediation.md` §9.4.

---

## 6. Configuring ADG to produce a plan at all

| Setting | Default | Notes |
| --- | --- | --- |
| `ADG_REMEDIATION_SIGNING_KEY` | *(empty)* | **A secret.** Empty means this deployment cannot export at all — deliberately: an unsigned change plan is indistinguishable from one somebody typed. Configuration, never source control |
| `ADG_REMEDIATION_EXECUTION_MODE` | `disabled` | The only value a production deployment may hold. `lab` selects a fixture-backed executor for development and is refused when `ADG_ENVIRONMENT=production` |
| `ADG_REMEDIATION_LAB_FIXTURE_PATH` | *(empty)* | Required in `lab` mode and meaningless otherwise |

Rotating the signing key changes `signature_key_id`, so documents signed with the old key
report *"signed with a key I do not have"* rather than failing verification ambiguously. Keep
the old key as long as any exported document is still in flight.

### Roles

Three people, and no role holds two of the three capabilities (ADR-0038):

| Who | Role | Does |
| --- | --- | --- |
| Planner | `remediation_planner` or `governance_admin` | Writes the plan, runs the simulation, submits it |
| Approver | `remediation_approver` | Approves or rejects |
| Operator | `admin` | Produces the signed export and carries it out |

A person holding two of these roles is still refused the second act. That is the control, not a
bug: an account able to write a plan and approve it could produce a fully documented removal of
anybody's access on its own.

### Can this deployment change my domain?

Ask it:

```
GET /api/v1/remediation/execution-policy
```

The answer is `can_execute: false`, with the list of things that would each have to be true
first. It comes from the executor the running process actually holds rather than from a
configuration file, which is the only version of that answer worth having.
