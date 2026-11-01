---
id: contract
title: Schema repo contract, version 1
status: current
created: 2026-11-01
updated: 2026-11-01
decisions: [ADR-0001]
---

# Schema repo contract, version 1

[ADR-0001](adr/adr-0001-dox-design.md) section 9 fixes what dox needs from
a schema repo: roles, not field names. This document fixes the keys that
declare those roles, in the schema repo's `dox.toml`, and the keys a
repository commits in `.dox.toml`. Examples use the fictional schema repo
`example-org/doc-schema`.

## Version

```toml
contract = "1.0"
```

`contract` is required in `dox.toml`. dox reads the major number and
refuses a major it does not support (exit 2). Minor versions add optional
keys only; a dox that does not know a key ignores it and says so in
`--json` under `config.unknown`.

## Schema repo: `dox.toml`

### Top level

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `contract` | string | yes | Contract version, `"<major>.<minor>"` |
| `name` | string | no | Display name for messages |
| `schemas` | string | no | Directory of `*.schema.json`, default `schemas` |
| `templates` | string | no | Directory of templates, default `templates` |
| `exclude` | list of glob | no | Paths no check reads in any repository (agent configuration, vendored trees); a repository's `exclude` adds to it |

Every `*.schema.json` under `schemas` is loaded into one registry by its
`$id`, so schemas may `$ref` each other.

### `[namespaces]`

Home name to repository. A home is a citation namespace and an id
allocation namespace.

```toml
[namespaces]
handbook = "example-org/handbook"
api      = "example-org/api"
work     = "example-org/work"
```

### `[types.<type>]`

One table per document type. The table name is the type name used on the
command line (`dox new adr <slug>`) and in `.dox.toml`.

| Key | Type | Required | Role | Meaning |
|-----|------|----------|------|---------|
| `kind` | `"canon"` or `"work"` | yes | | Canon says what is true; work is in flight. Drives `no-work-ids` and `no-plans-in-canon` |
| `schema` | string | yes | | Schema file, relative to `schemas` |
| `identity` | string | yes | identity | Frontmatter field holding the id; must equal the filename stem, or the directory name for a directory document |
| `id_format` | string | when allocated | placement | How `next` builds an id; tokens below. Omit for author-chosen ids |
| `id_legacy` | list of regex | no | placement | Frozen id shapes accepted but never allocated from |
| `cite` | string | no | identity | Short citation form, tokens below (`"ADR-{n:04}"`). Omit when the id itself is the citation |
| `allocate` | `"namespace"` or `"month"` | when `id_format` has `{n}` | placement | `{n}` counts across the whole home, or restarts each `{yymm}` cohort |
| `dirs` | list of glob | yes, unless `fallback` | placement | Where live documents of this type sit, relative to the repository's docs root. An entry naming a directory matches documents directly inside it; an entry ending in `.md` matches that file |
| `retired_dir` | string | no | placement | Where documents in a `retired` status must sit |
| `archive_dir` | string | no | placement | Where documents in a `closed` status must sit; may use `{yymm}` (month closed) |
| `scan_dirs` | list of glob | no | placement | Extra directories `next` and `id-unique` count, for homes not yet normalised |
| `layout` | `"file"`, `"dir"` or `"either"` | no | placement | `<id>.md`, `<id>/README.md`, or both; default `either` |
| `fallback` | bool | no | placement | Claims any Markdown file with frontmatter that no other type claims, anywhere in the repository. At most one type |
| `status` | table or `"none"` | yes | status | See below |
| `links` | table | no | links | Frontmatter field to link meaning; see below |
| `review_date` | string | no | review date | Field holding the next review date, read by `stale` (a later release) |
| `index` | string | no | | File in each live directory that receives a generated index block |
| `template` | string | no | | File template for `dox new`, relative to `templates` |
| `template_dir` | string | no | | Directory template for `dox new --dir` |

Id tokens: `{n}` or `{n:04}` (number, zero padded to the width given),
`{yymm}` (allocation month), `{slug}` (kebab case). dox derives the
matching regex from `id_format`; ids matching an `id_legacy` pattern are
accepted by `lint` and ignored by `next`. Globs use `*` within a path
segment, `**` across segments, and `{yymm}` for any month.

A file is a document of a type when its identity (stem, or directory name
for `<id>/README.md`) matches the type's `id_format` or `id_legacy`, or,
for a type without them, when it sits where `dirs`, `retired_dir` or
`archive_dir` say. Types are tried in declaration order. The `fallback`
type takes any other Markdown file with frontmatter under the docs root.

Templates may use `{{ID}}`, `{{DATE}}`, `{{TITLE}}`, `{{SLUG}}`,
`{{HOME}}` and `{{TYPE}}`.

#### `status`

```toml
[types.adr.status]
field = "status"
[types.adr.status.values]
proposed   = "discussion"
accepted   = "in-force"
rejected   = "retired"
superseded = "retired"
```

Each allowed value is tagged with one role: `discussion`, `in-force`,
`retired` or `closed`. `status = "none"` declares a type without status
(a versioned rules file). The JSON Schema should enumerate the same
values; dox reports a configuration error when the two disagree.

#### `links`

```toml
[types.adr.links]
supersedes      = "replaces"
superseded-by   = "replaced-by"
absorbed-into   = "absorbed-into"
decisions       = "cites"
refs            = "cites"
```

Meanings: `replaces`, `replaced-by`, `absorbed-into`, `cites`, `outcome`.
A type that declares `replaces` must declare `replaced-by` too; `lint`
checks both sides of each pair. A field may hold one ref or a list. A
document with a non-empty `absorbed-into` field counts as `retired` for
placement whatever its status.

### `[refs]`

How a ref string in a link field resolves.

| Key | Type | Meaning |
|-----|------|---------|
| `separator` | string | Between home and ref: `"handbook:ADR-0007"`. Default `":"` |
| `external` | list of tables | Refs that are not documents; `lint` accepts them unresolved |

```toml
[[refs.external]]
pattern = '^EX-[0-9]+$'           # tracker issues
url     = "https://tracker.example.org/issue/{0}"
```

A ref resolves, in order, as: an `external` pattern; an https URL; a
`[home<sep>]` citation in any type's `cite` form; a `[home<sep>]` id; a
`[home<sep>]` repository-relative path with an optional `#anchor`. A ref
without a home resolves in the citing document's own home.

### `[links]`

| Key | Type | Meaning |
|-----|------|---------|
| `cross_repo` | string | URL shape of a link into another home's repository, tokens `{repo}`, `{ref}`, `{path}`. Example `"https://github.com/{repo}/blob/{ref}/{path}"` |

`links-local` checks relative links; `links-remote` checks `cross_repo`
URLs and homed refs against each home's default branch.

### `[severity]` and `[checks.<id>]`

```toml
[severity]
schema       = "error"
links-remote = "warn"

[checks.no-work-ids]
patterns = [
  { regex = '\bEX-[0-9]+\b', what = "tracker id" },
  { regex = '\bplan-[0-9]{4}\b', what = "plan id" },
]

[checks.no-plans-in-canon]
dirs = ["**/plans/**", "**/status/**"]
```

`[severity]` gives each check a default of `error`, `warn` or `off`. A
check the schema repo does not list is `off`, so a check added in a later
dox release is opt-in for schema repos that predate it. `[checks.<id>]`
holds a check's options; the options each check takes are listed in the
check reference.

### `[index]`

| Key | Type | Meaning |
|-----|------|---------|
| `digest` | string | Path, relative to the docs root, of the generated agent digest. Omit for none |
| `digest_roles` | list | Status roles the digest lists, default `["in-force", "discussion"]` |

A type's `index` file is generated in each of its literal `dirs` that
exists. Generated blocks sit between `<!-- dox:index:begin -->` and
`<!-- dox:index:end -->`; text outside them is the author's. `index-fresh`
fails when a regenerated block differs. Links inside generated blocks do
not count as inbound links for `graph` orphans.

## Repository: `.dox.toml`

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `schema_ref` | string | yes | Tag or commit of the schema repo |
| `namespace` | string | yes | This repository's home; must be a key of `[namespaces]` |
| `root` | string | yes | Docs root, relative to the repository root (`"docs"` or `"."`) |
| `types` | list | yes | Types this repository holds; each must exist in `dox.toml` |
| `exclude` | list of glob | no | Paths no check reads (vendored trees, fixtures) |
| `baseline` | string | no | Baseline file path; absent means no baseline |
| `[severity]` | table | no | Per-check override of the schema repo default |
| `[checks.<id>]` | table | no | Per-check options; each key replaces the schema repo's |

```toml
schema_ref = "v1.0.0"
namespace  = "api"
root       = "docs"
types      = ["adr", "arc", "named-doc", "rules"]
exclude    = ["vendor/**"]
baseline   = ".dox-baseline.json"

[severity]
links-remote = "error"

[checks.no-work-ids]
exempt = ["vectors/**"]
```

The repository names the schema repo's version, never the schema repo
itself (ADR-0001 section 2).

## Environment and workspace

These set mechanics and topology only. None changes a lint result.

| Variable | Workspace key | Meaning |
|----------|---------------|---------|
| `DOX_SCHEMA_REPO` | | `<owner>/<repo>`; `--dox-schema` overrides |
| `DOX_SCHEMA_PATH` | | A local checkout used instead of fetching `schema_ref`, for a schema repo testing itself before tagging. Reported in the output like `--skip` |
| `DOX_SCHEMA_URL` | | Clone URL template for the schema repo, default `https://github.com/{repo}.git`. `GH_TOKEN` authenticates GitHub fetches |
| `DOX_CACHE_DIR` | `cache_dir` | Schema cache, default `$XDG_CACHE_HOME/dox` |
| `DOX_CLONES` | `clones` | Where sibling clones live: a path template with `{repo}` (repository name) or `{home}`. Remote checks read a sibling clone's `origin` default branch before falling back to the GitHub API |
| `DOX_TIMEOUT` | `timeout` | Seconds per network call, default 20 |

The workspace file is `$XDG_CONFIG_HOME/dox/config.toml`
(`~/.config/dox/config.toml`).

## Check reference

| Id | Offline | Options | Finds |
|----|---------|---------|-------|
| `config` | yes | | Configuration the run could still continue past: unknown keys, schema and `status.values` disagreeing |
| `schema` | yes | | Frontmatter missing, unparsable, or invalid against the type's schema |
| `id-unique` | yes | `allow` (list of citations) | Two documents in one home with the same id or citation number; identity field not equal to the stem |
| `links-local` | yes | `forbid` (list of URL substrings) | Relative links and homeless refs that do not resolve; forbidden link targets; a `replaces` without its `replaced-by` |
| `placement` | yes | | A document outside its type's `dirs`; a `retired` status outside `retired_dir`; a `closed` status outside `archive_dir`; a non-`README.md` part with frontmatter |
| `index-fresh` | yes | | A generated index or digest differs from what `dox index` would write |
| `no-work-ids` | yes | `patterns`, `exempt` | Pattern matches in the prose (frontmatter excluded) of Markdown files that are not work documents, in a repository whose `types` hold no work type |
| `no-plans-in-canon` | yes | `dirs` | Work-kind documents, or Markdown files matching `dirs`, in a repository whose `types` hold no work type |
| `links-remote` | no | | Homed refs, and `cross_repo` URLs on a default branch (`main`, `master`, `HEAD`), that do not exist on the target home's default branch |

A document whose type is not in the repository's `types` is reported once,
by `placement` (canon) or `no-plans-in-canon` (work), and skipped by the
other checks.
