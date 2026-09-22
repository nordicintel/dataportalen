# Releasing

Publishing to PyPI takes one file edit and one GitHub Release.

**Pushing to `main` never publishes anything.** The only workflow that uploads
is `Release`, and it runs only when a GitHub Release is published (or when you
run it by hand with the dry-run switch turned off).

## Cut a release

**1. Bump the version.** One file, one line:

```
src/dataportalen/_version.py   ->   __version__ = "0.2.0"
```

That is the single source of truth. `pyproject.toml` reads it, and so does the
User-Agent the client sends. There is nothing else to keep in sync.

**2. Merge it to `main`.**

**3. Publish a GitHub Release** with the tag `v<version>`:

```bash
gh release create v0.2.0 --title "v0.2.0" --notes "What changed"
```

Or in the browser: **Releases → Draft a new release**, tag `v0.2.0`, target
`main`, write the notes, **Publish release**.

**4. Watch it.** **Actions → Release.** It verifies the tag matches
`_version.py`, runs the tests, builds, and uploads to PyPI.

## Rehearse first

**Actions → Release → Run workflow.** `dry_run` is on by default: it builds,
validates the metadata and checks the wheel imports, then stops without
uploading. Use it to confirm a version bump before committing to a real
release.

## Version numbers

`MAJOR.MINOR.PATCH`:

- **patch** — bug fixes, vocabulary table refresh
- **minor** — new methods or fields, backwards compatible
- **major** — anything that breaks existing callers

The tag must be `v` plus exactly the version in `_version.py`. If they
disagree the workflow stops before uploading, because the alternative — a
wrong version on PyPI — cannot be undone.

## Requirements

A `PYPI_TOKEN` repository secret containing a PyPI API token (starts with
`pypi-`): **Settings → Secrets and variables → Actions**.

## If a release fails

| Failure | What to do |
| --- | --- |
| Version check fails | Fix `_version.py` or re-tag; nothing was uploaded |
| Tests or build fail | Fix, merge, delete the release and tag, start again |
| **Upload fails partway** | **Do not retry the same version.** PyPI refuses re-uploads of a version, even a deleted one. Bump the patch version and release again |

## Refreshing the vocabulary table

The label table is generated, not hand-written. When the registry gains new
vocabulary values:

```bash
python tools/build_vocabulary.py --sample 5000
```

It rewrites `src/dataportalen/vocabulary.json`, reports label coverage against
a random sample of live datasets, and lists whatever it could not resolve.
Commit the result and release it as a patch version.
