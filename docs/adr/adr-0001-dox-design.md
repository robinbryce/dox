---
id: adr-0001-dox-design
title: dox design — a generic docs checker driven by a schema repo
status: discuss
created: 2026-11-01
updated: 2026-11-01
categories: [DESIGN, CONFIG, CI, PACKAGING]
---

# ADR-0001: dox design

## Context

Teams that keep decisions, designs, plans and living documents in git
write conventions for them: where each kind of document lives, how ids are
allocated, what the header says, which status values exist, how a document
retires. Without a mechanism that checks them, those conventions decay at
the rate of the busiest repository: duplicate ids, several header formats,
broken relative links, superseded documents left beside current ones, and
hand-maintained indexes that are always stale.

The checks are mostly mechanical and the same everywhere. What differs
between organisations is the vocabulary: which document types exist, their
frontmatter schemas, their directories, status values and id patterns.

The first adopter is the [Forestrie](https://github.com/forestrie)
organisation, whose documentation decision (devdocs ADR-0071) needs a CI
gate in every repository, safe id allocation, and lifecycle commands. The
same tool should serve unrelated organisations and personal repositories
without changes to its code.

The design has to settle what is expensive to change once many
repositories' CI depends on it: what decides lint results, how
configuration is layered, how the tool is installed in CI, and its command
and output contract. Internals are left to the implementation.

## Decision

### 1. dox is generic; a schema repo supplies the vocabulary

The tool contains no organisation's document types. Everything that gives
documents meaning comes from a **schema repo**: a git repository named
`<owner>/<repo>` (an organisation or a user) that holds

- `dox.toml`, the preset: document types, and for each its schema file,
  directories, id pattern, status values, retired and archive directories;
  the namespace map from home names to repositories (`canopy` →
  `example-org/canopy`); link policy; check severities;
- `schemas/*.schema.json`, JSON Schema draft 2020-12, one per document
  type;
- `templates/`, scaffolds for `dox new`.

### 2. The schema repo is named explicitly, never guessed

- `--dox-schema <owner>/<repo>` names the schema repo for one invocation.
- `DOX_SCHEMA_REPO` supplies the default when the flag is absent.
- If neither is set, dox exits with a configuration error (exit code 2)
  and a message saying how to set one. There is no implicit schema repo.

Each person sets `DOX_SCHEMA_REPO` per workspace in their own environment
(dotfiles, profile). CI sets it in the workflow, normally through the
GitHub Action's `schema-repo` input. The committed repository
configuration does not name the schema repo; it pins its version
(section 3).

### 3. Only committed configuration decides results

Layers, highest precedence first:

| Layer | Source | May set |
|-------|--------|---------|
| Environment | `DOX_*` variables, CLI flags | mechanics, topology, `DOX_SCHEMA_REPO` |
| Workspace | the user's profile or dotfiles | topology only: where sibling clones live, cache location |
| Repository | committed `.dox.toml` | `schema_ref`, the document types this repo uses, its namespace, per-check severity, baseline path |
| Schema repo | `dox.toml` at `schema_ref` | document semantics (section 1) |
| Built-in | the tool | mechanics only: cache directory, timeouts, output format |

- `.dox.toml` must set `schema_ref`: a tag or commit of the schema repo.
  A schema change reaches a repository only when that repository bumps its
  pin, so one change cannot break every repository's CI at once.
- Document semantics come only from the repository and schema-repo
  layers. Built-ins never fill a gap there; a missing semantic setting is
  a configuration error, not a silent default.
- The environment and workspace layers cannot change what is valid. The
  same commit gives the same lint result on any machine and in CI. The one
  exception is `--skip` (section 6), which is always reported.

The schema repo is fetched at `schema_ref` (shallow, by git) into a cache
keyed by `<owner>/<repo>/<ref>`. Private schema repos use the caller's git
credentials, or `GH_TOKEN` in CI.

### 4. Python, uv and jsonschema

- Python 3.11 or later (for `tomllib`). Dependencies: `jsonschema` (with
  `referencing`) and `PyYAML` for frontmatter. Nothing else.
- Installed as a uv tool from a tag:
  `uv tool install git+https://github.com/robinbryce/dox@vX.Y.Z`.
- This repository also ships a composite GitHub Action
  (`robinbryce/dox@vX.Y.Z`) with inputs `schema-repo` and `args`.
- Releases are semver tags. A repository pins both the tool version (in
  its workflow) and the schema version (`schema_ref`).

### 5. Command surface

| Command | Does |
|---------|------|
| `dox lint [--changed] [--remote]` | Run the checks; exit non-zero on errors |
| `dox next <type>` | Allocate the next free id in the namespace |
| `dox new <type> <slug>` | Allocate an id and scaffold from the schema repo's template |
| `dox graph [--json\|--mermaid\|--dot]` | The reference graph: backlinks, orphans, supersession chains, links into retired docs |
| `dox index` | Regenerate the indexes and the compressed agent digest the schema repo asks for |
| `dox retire <id> --by <id>\|--absorbed-into <ref>` | Write both link fields, move to the retired directory, rewrite inbound links |
| `dox close <plan>` | Refuse unless every `outcome` ref exists on its home's default branch; then complete and archive |
| `dox context <path>` | Emit the minimal document set for work touching a path |
| `dox stale` | Report documents past review dates, idle plans, live links to retired docs |

The command names and their meanings are fixed by this decision.
Arguments may grow.

### 6. Checks are offline or remote, can be skipped, and ratchet

- Every check has a stable id (for example `schema`, `id-unique`,
  `links-local`, `links-remote`, `placement`, `index-fresh`,
  `no-work-ids`, `no-plans-in-canon`).
- Each check is **offline** (reads only the working tree and the cached
  schema repo) or **remote** (reads other repositories, `origin` or open
  pull requests). `lint` runs offline checks by default; `--remote` adds
  remote ones. `next` and `close` are inherently remote.
- `--skip <check>[,<check>…]` is the escape hatch. Skipped checks are
  named in the output and in `--json`, so a skip in a CI workflow is
  visible in review.
- `.dox.toml` sets each check's severity: `error`, `warn` or `off`.
- A committed baseline file lists known violations. Findings in the
  baseline are reported but do not fail. New findings fail. The baseline
  may only shrink: `dox lint --update-baseline` removes entries that no
  longer occur and never adds any. A repository can therefore turn the
  gate on before it is clean, and cannot regress.

### 7. Output is written for agents as well as people

- Every finding carries its check id, `path:line`, a one-line message and
  a fix: the concrete edit or command that resolves it.
- `--json` emits the same findings, plus skipped checks and the resolved
  configuration with the source of each value.
- Exit codes: 0 clean (warnings allowed), 1 errors found, 2 usage or
  configuration error (including a missing schema repo), 3 a remote check
  could not reach its source.
- An MCP server is deferred; `graph --json` and `context` cover the
  known agent uses.

### 8. Packaging boundary

- This repository: the CLI, the GitHub Action, its tests and this
  decision log. No organisation's presets, schemas or examples beyond a
  neutral fictional one (`example-org/doc-schema`) used by the tests.
- Schema repos: owned by each organisation.
- Agent skills and hooks that call dox live with the agent configuration
  that uses them, not here.

### 9. Scope of this decision

This decision fixes sections 1 to 8. Left to the implementation:
internal module layout, the exact `dox.toml` and `.dox.toml` keys, the
cache format, how remote checks fetch sibling trees (git or the GitHub
API), how a schema repo lints itself against a local checkout before
tagging, and the index and digest formats.

## Consequences

**Positive**

- One tool serves any number of organisations; adding one is a new
  schema repo, not a code change.
- Local runs, teammates' runs and CI agree, because only committed
  configuration decides results.
- Schema changes roll out repository by repository through `schema_ref`.
- The baseline lets a large existing corpus adopt the gate immediately.
- Skips and severities are explicit and reviewable.

**Negative**

- Every invocation needs `DOX_SCHEMA_REPO` or `--dox-schema`; a
  newcomer's first run fails until they set it. The error says how.
- Two pins to bump per repository (tool version and `schema_ref`).
- A private schema repo needs credentials in CI.
- Remote checks depend on network and tokens, which is why they are
  separate and opt-in.

## Alternatives Considered

- **An organisation-specific tool kept with that organisation's agent
  configuration.** Simplest for one organisation; duplicates the
  mechanics for every other one, and ties public repositories' CI to a
  private repository.
- **Schema and preset in each repository's `.dox.toml`.** Self-contained,
  but every repository carries a copy that drifts; the shared vocabulary
  is the point.
- **Infer the schema repo** from the git remote's owner. Convenient, but
  wrong for forks, personal repositories and multi-organisation
  workspaces, and silent when wrong.
- **Track the schema repo's default branch.** No pin to bump, but one
  merge there changes every repository's CI result at once.
- **Go single binary.** Easy distribution, but the existing seed scripts
  are Python, and `uv tool install` makes Python distribution cheap.
- **Built-in defaults for document semantics.** Lets dox run with no
  configuration, but hides which rules apply and lets local and CI drift
  when a layer is missing.
- **An MCP server from the start.** Live queries for agents; deferred
  until `graph --json` and `context` prove insufficient.

## References

- [Forestrie devdocs ADR-0071: Documentation system v2](https://github.com/forestrie/devdocs/blob/main/adr/adr-0071-documentation-system-v2/README.md),
  the first adopter's documentation decision.
- [forestrie/dox](https://github.com/forestrie/dox), the first schema
  repo.
- [JSON Schema 2020-12](https://json-schema.org/draft/2020-12)
- [uv tools](https://docs.astral.sh/uv/concepts/tools/)
