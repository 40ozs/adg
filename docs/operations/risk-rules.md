# Risk rules: configuration reference

ADG's risk findings come from eleven deterministic rules. Each one matches a shape in the
permission data ADG has collected, cites the records it matched, and can be re-derived from
those records months later. There is no score and no model anywhere in it.

This document is the operator's reference: what each rule is worth by default, what you can
change, and the one thing ADG cannot work out for itself.

> **The thing ADG cannot work out for itself is which data matters.** It can see a folder
> called `HR` and it will conclude nothing from that, because a folder called `HR` in a test
> estate holds nothing and a folder called `Misc` holds the payroll. The
> `broad_access_on_sensitive_resource` rule therefore reports **nothing at all** until you
> declare which resources are sensitive. That silence is a configuration state, not a clean
> result. See [ADR-0024](../decisions/0024-sensitivity-is-declared-not-inferred.md).

---

## 1. Turning it on

Nothing is required. With no configuration at all, every rule runs at the severities in the
table below and the sensitive-resource rule reports nothing.

To change anything, write a JSON file and point `ADG_RISK_CONFIGURATION_PATH` at it:

```bash
ADG_RISK_CONFIGURATION_PATH=/etc/adg/risk.json
```

A path that is set and cannot be read is a **startup error**, not a fall back to the
defaults. An operator who configured a policy and mistyped the path would otherwise get a
report produced by settings they never wrote.

---

## 2. The file

Every part is optional.

```json
{
  "version": "2026-q3",
  "rules": {
    "everyone_broad_access": {
      "enabled": true,
      "severities": { "read": "critical" },
      "options": { "minimum_category": "modify" }
    },
    "direct_user_ace": { "enabled": false }
  },
  "sensitive_resources": [
    { "label": "Payroll", "path_prefix": "\\\\FS01\\Finance\\Payroll" },
    { "label": "Legal hold", "share_key": "fs02|legal" }
  ]
}
```

`version` is your own label for the policy. ADG does not interpret it; it records it on every
evaluation so a report can say which settings produced it.

**Mistakes are refused, not ignored.** An unknown rule name, an option a rule does not have,
a severity band a rule never reports, or a misspelled selector key all fail the load and name
the entry. A configuration file that silently drops the one sensitive resource somebody cared
about is worse than one that will not start.

---

## 3. The rules, their default severities, and their thresholds

| Rule | Subject | Bands and default severities | Options (default) |
| --- | --- | --- | --- |
| `everyone_broad_access` | resource | `read` = **high**<br>`write` = **critical**<br>`full_control` = **critical**<br>`null_dacl` = **critical** | `minimum_category` = `"read"` |
| `authenticated_users_broad_access` | resource | `read` = **medium**<br>`write` = **high**<br>`full_control` = **high** | `minimum_category` = `"read"` |
| `domain_users_broad_access` | resource | `read` = **low**<br>`write` = **medium**<br>`full_control` = **high** | `minimum_category` = `"read"` |
| `direct_user_ace` | access | `default` = **low** | — |
| `disabled_principal_retains_access` | access | `default` = **medium** | `max_membership_depth` = `8` |
| `unresolved_sid_on_acl` | access | `default` = **medium** | — |
| `broken_inheritance` | resource | `protected` = **low**<br>`diverged` = **informational** | `include_diverged` = `true`<br>`include_share_roots` = `false` |
| `deep_group_nesting` | principal | `default` = **low** | `max_depth` = `3` |
| `redundant_access_paths` | access | `default` = **informational** | `minimum_paths` = `2`<br>`max_membership_depth` = `8` |
| `empty_permission_bearing_group` | principal | `default` = **low** | — |
| `broad_access_on_sensitive_resource` | access | `write` = **high**<br>`full_control` = **critical** | `minimum_category` = `"modify"`<br>`broad_trustee_sids` = `["S-1-1-0", "S-1-5-11", "S-1-5-32-545"]`<br>`broad_trustee_rids` = `[513, 515]` |

Severities are `informational`, `low`, `medium`, `high`, `critical`. They **rank**; they do
not add up. ADG counts and sorts findings and never sums them into a score, because a number
with no unit is a number people compare between two shares.

### What the bands mean

A **band** is a fact about the finding — *the grant reached Full Control*, *there is no DACL
at all* — chosen by the rule. What that fact is worth is chosen by you. That split is why
lowering a severity never changes what a rule matches, and why a noisy rule is quieted by
re-grading it rather than by making it stop looking.

| Band | Meaning |
| --- | --- |
| `read` | The grant reaches a read category and no further. |
| `write` | The grant reaches Write or Modify. |
| `full_control` | Full Control, including the rights to rewrite the ACL and take ownership. |
| `null_dacl` | There is no DACL at all, which grants everyone full access. |
| `protected` | `SE_DACL_PROTECTED`: the directory refuses entries its parent passes down. |
| `diverged` | The DACL is not what the parent projects, without protection being set. |
| `default` | The rule has only one shape. |

### The thresholds

* **`minimum_category`** — the lowest rights category that counts. One of `traverse`, `read`,
  `write`, `read_execute`, `modify`, `full_control`. A mask must contain **every** bit the
  category requires, so a threshold never labels access that is not there. Raising
  `everyone_broad_access` to `"modify"` is the usual first tuning step on an estate where a
  readable public share is normal.
* **`max_depth`** (`deep_group_nesting`) — chains longer than this are reported. The default
  of 3 means *a group inside a group inside a group inside the granted group* is the first
  thing reported.
* **`minimum_paths`** (`redundant_access_paths`) — how many distinct **trustees on the access
  control list** must reach one principal. Two chains to the same trustee are one route:
  removing one changes nothing while the trustee is still on the list, and the point of the
  finding is that a removal will not take effect.
* **`max_membership_depth`** — how far below a trustee the rule expands group membership.
* **`include_share_roots`** / **`include_diverged`** (`broken_inheritance`) — share roots are
  excluded by default because a share root is a boundary by definition; divergence is
  included because it is the ordinary finding.
* **`broad_trustee_sids` / `broad_trustee_rids`** — which trustees count as "broad" for the
  sensitive-resource rule. RIDs are listed separately because `Domain Users` is
  `<domain SID>-513` and so is a different string in every domain.

---

## 4. Declaring sensitive resources

Each entry sets a `label` and **exactly one** selector:

| Selector | Matches |
| --- | --- |
| `path_prefix` | A directory and everything beneath it. Compared by whole path segment, case-insensitively: `\\FS01\Finance` covers `\\FS01\Finance\Payroll` and does **not** cover `\\FS01\Finance-Archive`. |
| `resource_key` | One directory, exactly. |
| `share_key` | One share, including its share-level ACL. |

The `label` is what a reader is shown in place of a judgment ADG cannot make, so write it for
the person reading the report: `"Payroll"`, `"Under legal hold"`, `"PCI scope"`.

A `path_prefix` never matches a share's own ACL — a share is not at a path, and treating the
two as interchangeable would make one tag mean different things on the two layers. Tag the
share with `share_key` if you mean the share ACL.

---

## 5. Confidence: what ADG will not claim

Severity is yours. **Confidence is not configurable**, because it is a statement about ADG's
own evidence rather than about how much the finding matters.

| Confidence | Means |
| --- | --- |
| `confirmed` | Every fact the rule read was observed directly by a collector and is complete. |
| `probable` | A fact was derived rather than read — usually a DACL projected onto a directory whose own descriptor nobody fetched. |
| `possible` | A fact is bounded or partial — a truncated group expansion, a projection from several levels up. |

A finding is always reported at the **weakest** confidence any one of its facts allows. One
projected DACL makes the whole finding rest on a projection.

### What ADG refuses to report at all

Some gaps are not weak findings; they are not findings. In each of these cases ADG produces
nothing, and this is deliberate:

* **A group whose membership no reconciling run enumerated** is never reported as empty. An
  empty group and an uncollected group are identical in storage, and the action this finding
  invites is removing a grant from a group that may have a hundred members in it.
* **A directory nobody read** is never reported as having no DACL. A NULL DACL is something a
  collector observed; an unread descriptor is unknown.
* **A share ACL nobody read** is never treated as a share that grants nothing.
* **A well-known SID with no principal record** — `Everyone`, `BUILTIN\Administrators` — is
  never reported as an orphan. It means the same thing on every Windows computer whether or
  not a run emitted a row for it.
* **Four of the seven ACL-boundary reasons** (`share_root`, `scan_root`, `parent_unreadable`,
  `parent_null_dacl`) describe how far the collector could see, not the estate's permissions,
  and never produce a broken-inheritance finding.

---

## 6. Running an evaluation

Three passes, differing only in what they cover:

| Pass | Loads | May resolve |
| --- | --- | --- |
| Full | The whole estate | Anything |
| Incremental | The facts around what one scan run changed | Only findings whose every subject it loaded |
| Targeted | The directories or shares you name | Only findings whose every subject it loaded |

**A pass never closes a finding it did not examine.** That is enforced rather than intended:
the scope is recorded on the evaluation row, and a resolution is written only for a finding
inside it whose rule actually ran. Without it, the first incremental pass after a collector
run would empty the report and look like an improvement.

Phase 8A ships the service layer only. There is no HTTP endpoint and no screen yet; an
evaluation is run from the backend. See the phase handoff for what comes next.

---

## 7. Checking a finding

Every finding stores the complete records it was computed from. `RiskService.verify` rebuilds
the facts from that evidence alone — no database read, no estate — re-runs the one rule that
produced it, and reports whether the same finding comes back.

This is how you answer *"was this actually true, on the evidence you kept?"* about a finding
from last quarter. A finding that does not reproduce is a problem with ADG rather than with
the estate: either the evidence was insufficient when it was written, or the rule's predicate
has since changed.
