# Contributing

Thanks for helping improve `pywencai-enhanced`.

## Development setup

Create and activate a virtual environment, then install the package in editable mode:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -e .
```

## Running tests

Run the standalone unit tests before opening a pull request:

```bash
python -m unittest tests.test_pywencai
```

## Node.js usage

Normal package users do not need Node.js at install time because this repository already includes `hexin-v.bundle.js`.

Node.js is only needed when maintainers want to rebuild the bundled token-generation script:

```bash
cd src/pywencai
npm install
npx webpack --config webpack.config.js
```

## Pull request guidance

- Keep API compatibility with `pywencai` unless the change clearly justifies a break.
- Prefer adding or updating fixture-driven tests for parser and retry changes.
- When touching auth or parser logic, describe the observed upstream behavior in the PR.
- Do not remove the attribution to the upstream `zsrl/pywencai` project.

## Commit guidance

- Use concise commit messages focused on the user-visible change.
- Group related documentation, packaging, and test updates together.
