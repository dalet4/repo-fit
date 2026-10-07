# Licence fit: spec

Status: draft, not built. This is a guide to what a licence means for how you use a repo. It is not legal advice.

## Problem

repo-fit asks one licence question today: is it non-commercial, missing or unclear? Everything else passes. Checked against the code (`gates()` in `scripts/repo_fit.py`) and live GitHub data on 2026-10-07:

1. **`NOASSERTION` is a dead end.** GitHub does not recognise the Business Source License, SSPL, the Elastic licence, the Functional Source License or Sustainable Use licences, so those repos come back as `NOASSERTION`. Real examples: `hashicorp/terraform`, `mongodb/mongo`, `elastic/elasticsearch`, `getsentry/sentry`, `n8n-io/n8n`, `redis/redis` and `gitlabhq/gitlabhq` all return `NOASSERTION`. The helper flags `licence-unclear` and tells the agent to open the LICENSE file, and the agent may or may not. These are the licences that most often bite a client project.
2. **Recognised restrictive ids pass silently.** If GitHub does return `AGPL-3.0`, `SSPL-1.0`, `BUSL-1.1` or `Elastic-2.0`, `gates()` returns `[]`. A unit test asserts GPL-3.0 passes. That is right for internal use and wrong for some client work.
3. **The answer depends on how you use the repo.** The only switch today is `--personal`. Copyleft is fine inside your own company, a problem when you ship code to a client, and AGPL differs again when you host it for them.

## Goal

For each candidate, say what the licence allows for the way the user will use it, from the licence text GitHub returns, and say plainly when that could not be determined.

## Non-goals

- No legal advice, and no "safe" verdict. Output says "this licence is X, which usually means Y", never "you may".
- No licence scan of the repo's own dependencies. One repo, one licence.
- No relicensing history in v1 (a repo that changed licence last year).

## Use modes

Replaces the single `--personal` flag. `--personal` stays as an alias for `--use personal`.

| Mode | Meaning |
| --- | --- |
| `personal` | Hobby or learning, nothing sold |
| `internal` | Used inside your own business, not distributed or hosted for others |
| `client` | Code or a deployed system handed to a client (distribution) |
| `saas` | Hosted for others over a network |

Default when not set: `client`, the strictest of the common cases, with the report saying which mode was assumed.

## Licence classes

`facts` adds `licence_class` and `licence_note` next to `licence`.

| Class | Examples | personal | internal | client | saas |
| --- | --- | --- | --- | --- | --- |
| `permissive` | MIT, Apache-2.0, BSD, ISC | ok | ok | ok | ok |
| `weak-copyleft` | LGPL, MPL-2.0 | ok | ok | review | review |
| `strong-copyleft` | GPL-2.0/3.0 | ok | ok | review | review |
| `network-copyleft` | AGPL-3.0 | ok | review | review | review |
| `source-available` | BUSL, SSPL, Elastic-2.0, FSL, Commons Clause | ok | review | gate | gate |
| `noncommercial` | CC-BY-NC, PolyForm-Noncommercial | ok | gate | gate | gate |
| `open-core-mixed` | root LICENSE says "portions licensed as follows" (n8n style) | review | review | review | review |
| `unknown` | none, unrecognised text | gate | gate | gate | gate |

`gate` is a hard Skip, as `gates` works today. `review` adds a `licence_review` entry that the judge must address in the report, and does not Skip by itself. A `review` repo cannot be High unless the reason names the licence and the use mode.

The table is data in one dict, not branching logic, so a wrong row is a one-line fix.

## Resolving `NOASSERTION`

When `spdx_id` is `NOASSERTION`, `Other` or missing, fetch `GET /repos/{slug}/license` (one extra API call) and match the decoded text against signature phrases:

| Phrase in LICENSE text | Class |
| --- | --- |
| "Business Source License" | source-available |
| "Server Side Public License" | source-available |
| "Elastic License" | source-available |
| "Functional Source License" | source-available |
| "Commons Clause" | source-available |
| "Sustainable Use License" | source-available |
| "Portions of this software are licensed as follows" | open-core-mixed |
| "Non-Commercial" / "NonCommercial" | noncommercial |

Rows are checked top to bottom in this order: `open-core-mixed` first (a mixed LICENSE can also name a source-available licence further down), then `noncommercial`, then `source-available`. The first match wins. No match means `unknown`, and `licence_note` quotes the first line of the file so the agent can read it. Matching is on text GitHub served, never on a search snippet.

## Output change

`facts` result gains `licence_class`, `licence_note`, `licence_review` (list) and `use_mode`. Existing keys are unchanged, so nothing downstream breaks. `licence-unclear` is kept for `unknown`.

SKILL.md changes: ask for or infer the use mode in the profile step, record it in `.repo-fit/profile.md`, and add a sentence to the judge step about `licence_review`.

## Tests

One test per behaviour, in the existing `tests/test_repo_fit.py` style with a faked API:

- each class gets one id, checked across all four modes (the table above);
- `NOASSERTION` plus a BUSL text resolves to `source-available`;
- `NOASSERTION` plus the n8n header resolves to `open-core-mixed`;
- `NOASSERTION` plus unrecognised text is `unknown` and quotes the first line;
- no licence endpoint answer (404) is `unknown`, not a crash;
- `--personal` still behaves as before;
- the GPL-3.0 test is updated: it passes for `internal`, and carries a review for `client`.

## Acceptance

1. `facts hashicorp/terraform --use client` reports `source-available` and a `gate`. Today it reports only `licence-unclear`.
2. `facts n8n-io/n8n` reports `open-core-mixed`.
3. `facts` on an MIT repo makes no extra API call.
4. All existing tests pass, plus the new ones.

## Open questions

1. Is `client` the right default? It is the safe one, and it will produce more review notes for people who only use tools internally.
2. Should `open-core-mixed` stay a `review`, or is it a `gate` for `saas`?
3. Do `internal` and `saas` need to be separate modes, or is that over-built for v1?

## Cost and limits

- One extra API call per repo, and only for `NOASSERTION`, `Other` or missing. How often that happens across real candidates is not measured. Count it on a few weekly runs before shipping.
- Phrase matching can be wrong on a heavily edited licence. Hence `licence_note` quotes the file, and `unknown` is a gate.
- A dual-licensed repo (for example MIT or Apache) is read from the primary LICENSE only.
