---
name: push-release
description: Cut a new release - bump the version in pyproject.toml, tag, and push to trigger PyPI publish and MCP Registry publish via GitHub Actions. Use when the user asks to release, ship a version, bump and tag, cut a hotfix, or publish to PyPI.
---

# Push Release

This project ships **one wheel** to PyPI containing all three console scripts (`mymcp`, `mymcp-api`, `mymcp-mcp`) at a single version. One tag push, one PyPI publish - there is no separate PyPI step for the API or MCP. Downstream users `pip install` the package and run whichever entry point they want; the API and MCP server aren't deployed by this repo. The MCP Registry entry refreshes automatically on the same tag (`mcp-registry-publish.yml`), so it's discovery metadata, not a second distribution.

## Workflow

Execute these steps in order. Confirm with the user before running `make bump_version` and before pushing - tags are public and a published version cannot be unpublished from PyPI.

### 1. Pre-flight

Make sure `main` is clean and CI passes locally:

```bash
git status                # working tree must be clean
git rev-parse --abbrev-ref HEAD   # must be on main
make ci && make test
```

If anything fails, stop and report. Do not proceed with a broken `main`.

### 2. Pick the bump level

Ask the user which bump if not specified:

- `BUMP=patch` (default) - bug fixes, no API change. `0.1.1 -> 0.1.2`
- `BUMP=minor` - additive features, backwards compatible. `0.1.1 -> 0.2.0`
- `BUMP=major` - breaking changes. `0.1.1 -> 1.0.0`

### 3. Bump the version

```bash
make bump_version BUMP=patch   # or minor / major
```

This:
- updates `pyproject.toml` `version = "x.y.z"`
- creates a `Release vX.Y.Z` commit
- creates an annotated tag `vX.Y.Z`

The Makefile target refuses to run if the working tree or staging area is dirty.

### 4. Push with tags

```bash
git push origin main --follow-tags
```

The tag push is what triggers the release - without `--follow-tags` only the commit is pushed and nothing publishes.

### 5. Verify

Two GitHub Actions workflows fire on the `v*` tag:

1. **Release** (`.github/workflows/release.yml`)
   - Runs `make ci` and `make test`
   - Builds the package with `uv build`
   - Publishes to PyPI via OIDC trusted publishing
   - Creates a GitHub Release with auto-generated notes, wheel, and sdist
2. **MCP Registry Publish** (`.github/workflows/mcp-registry-publish.yml`)
   - Authenticates via GitHub OIDC
   - Publishes the MCP server to the MCP Registry

Check:
- GitHub Actions tab - both workflows green
- GitHub Releases - new `vX.Y.Z` entry with assets
- [MCP Registry](https://registry.modelcontextprotocol.io) - updated server entry

## Hotfix release

For an urgent fix on top of an existing release without picking up unrelated `main` changes:

```bash
git checkout -b hotfix/<description> v0.1.1
# apply the fix
make ci && make test
# open a PR to main, squash-merge it
git checkout main && git pull
make bump_version BUMP=patch
git push origin main --follow-tags
```

## Version is single-sourced

The version lives in **one place**: `pyproject.toml` -> `version = "x.y.z"`. All three interfaces read it at runtime via `importlib.metadata.version()`. Never hand-edit version strings in source files.

## PyPI trusted publishing (one-time setup)

Only relevant if PyPI publishing has never been configured for this repo. The release workflow uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API tokens are needed:

1. Create the package on [pypi.org](https://pypi.org/manage/projects/).
2. Add a trusted publisher under the package settings:
   - Owner: GitHub username or org
   - Repository: GitHub repository name
   - Workflow: `release.yml`
   - Environment: `release`
3. Create a `release` environment in GitHub (Settings -> Environments).

After this is done once, every tag push auto-publishes.

## What can go wrong

- **Working tree not clean** - `make bump_version` aborts. Commit or stash first.
- **Pushed commit without tag** - nothing publishes. Push the tag separately: `git push origin vX.Y.Z`.
- **CI fails inside `release.yml`** - the PyPI publish step never runs, but the tag exists. Fix the cause on `main`, then either bump again to a new patch or delete the tag (`git push --delete origin vX.Y.Z`) and re-run after fixing - delete only if no one has pulled it.
- **PyPI publish succeeds but MCP Registry fails** - the version is live on PyPI; fix the registry workflow and re-run it via workflow dispatch.
