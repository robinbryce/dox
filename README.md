# dox

A docs-as-code checker for repositories that keep decisions, designs, plans
and living documents in git. It validates frontmatter against per-type
schemas, keeps ids unique and links resolvable, places retired documents,
allocates ids safely, and generates indexes.

dox is generic. The document types, schemas and rules come from a **schema
repo** you name with `--dox-schema <owner>/<repo>` or `DOX_SCHEMA_REPO`.

Design: [ADR-0001](docs/adr/adr-0001-dox-design.md). What a schema repo
and a repository declare: [the contract](docs/contract.md).

## Install

```bash
uv tool install git+https://github.com/robinbryce/dox@v0.1.0
export DOX_SCHEMA_REPO=example-org/doc-schema   # or pass --dox-schema
```

A repository commits a `.dox.toml` that pins the schema repo's version:

```toml
schema_ref = "v1.0.0"
namespace  = "handbook"
root       = "docs"
types      = ["adr", "rules", "note"]
baseline   = ".dox-baseline.json"
```

## Commands

| Command | Does |
|---------|------|
| `dox lint [--changed] [--remote] [--skip a,b] [--json]` | Run the checks. Offline by default; `--remote` adds checks that read other repositories |
| `dox lint --init-baseline` / `--update-baseline` | Record today's findings so the gate can go on before the docs are clean; the baseline only ever shrinks |
| `dox next <type> [slug]` | The next free id, counted across the working tree, `origin`'s default branch and open pull requests |
| `dox new <type> <slug> [--dir]` | Allocate an id and scaffold the document from the schema repo's template |
| `dox graph [--json\|--mermaid\|--dot]` | Typed links and Markdown links: backlinks, orphans, supersession chains, links into retired documents |
| `dox index [--check]` | Regenerate the indexes and digest the schema repo asks for |

Every finding has a check id, `path:line`, a message and a fix. Exit codes:
0 clean (warnings allowed), 1 errors, 2 usage or configuration error, 3 a
remote source could not be reached.

## GitHub Action

```yaml
- uses: actions/checkout@v4
- uses: robinbryce/dox@v0.1.0
  with:
    schema-repo: example-org/doc-schema
    args: lint --changed
```

`token` (default `github.token`) must be able to read a private schema
repo.

## Development

```bash
uv run python -m unittest discover -s tests -t .
```

The tests build temporary git repositories with a bare `origin`, use the
fictional schema repo in [examples/doc-schema](examples/doc-schema), and
never touch the network.

## License

MIT
