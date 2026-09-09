# Development

## Environment

```bash
git clone https://github.com/goncalorafaria/rexs.git
cd rexs
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,docs]'
```

## Checks

```bash
pytest -q
ruff check src tests
python -m compileall -q src tests
mkdocs build --strict
```

Tests use fake Slurm command runners and temporary SQLite databases. They do
not require a Slurm installation.

## Project layout

```text
src/rexs/
├── cli.py          # Python Fire command surface
├── compiler.py     # Beaker v2 to sbatch translation
├── config.py       # profile, mappings, and substitutions
├── controller.py   # squeue/sacct/scancel reconciliation
├── dryrun.py       # corpus discovery and shell validation
├── state.py        # SQLite schema and records
├── server.py       # HTTP API and background process
└── web.py          # dashboard application
```

## Documentation

Preview the site while editing:

```bash
mkdocs serve
```

Every push to `main` runs the test workflow and publishes the MkDocs site
through the official GitHub Pages actions workflow. Pull requests build the
documentation without deploying it.

## Release shape

The package uses `setuptools` and exposes one console script, `rexs`. Build
artifacts with:

```bash
python -m build
python -m twine check dist/*
```

