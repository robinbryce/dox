# dox

A docs-as-code checker for repositories that keep decisions, designs, plans
and living documents in git. It validates frontmatter against per-type
schemas, keeps ids unique and links resolvable, places retired documents,
allocates ids safely, and generates indexes.

dox is generic. The document types, schemas and rules come from a **schema
repo** you name with `--dox-schema <owner>/<repo>` or `DOX_SCHEMA_REPO`.

Status: design. See [ADR-0001](docs/adr/adr-0001-dox-design.md).

## License

MIT
