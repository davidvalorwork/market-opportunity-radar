# Build context = repo root. Succeeds only if the OpenRouter adapter tests and the
# contract tests pass against the INSTALLED wheel, offline (no real API, no key).
# docker build -f docker/llm-test.Dockerfile -t market-radar/b6-llm:test .
FROM python:3.11.17-slim@sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51
WORKDIR /repo
COPY pyproject.toml requirements.lock LICENSE NOTICE ./
COPY src/ src/
# Build isolation fetches the setuptools version pinned in pyproject.toml; test tools come from requirements.lock.
RUN pip wheel --no-cache-dir --no-deps --disable-pip-version-check -w /dist . \
    && python -m venv /opt/radar \
    && /opt/radar/bin/pip install --no-cache-dir --no-deps --disable-pip-version-check -r requirements.lock /dist/radar-0.0.0-py3-none-any.whl \
    && /opt/radar/bin/pip check --disable-pip-version-check
# Run from a directory without src/ or pyproject.toml so only the installed package is importable.
COPY contracts/examples/ /check/contracts/examples/
COPY contracts/capabilities.json /check/contracts/
COPY tests/test_contracts.py /check/tests/
COPY tests/openrouter/ /check/tests/openrouter/
WORKDIR /check
RUN --network=none /opt/radar/bin/python -c "import radar.adapters.openrouter.client as c; assert '/opt/radar/' in c.__file__, c.__file__; print('radar from', c.__file__)" \
    && /opt/radar/bin/python -m pytest -q -p no:cacheprovider tests/openrouter tests/test_contracts.py
USER nobody
