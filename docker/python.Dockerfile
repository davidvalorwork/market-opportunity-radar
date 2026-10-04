# Build root context, installed wheel, pinned runtime + full dependency lock.
# Offline domain + contracts test gate is mandatory during build and runtime.
FROM python:3.11.17-slim@sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51
WORKDIR /repo
COPY pyproject.toml requirements.lock LICENSE NOTICE ./
COPY src/ src/
RUN pip wheel --no-cache-dir --no-deps --disable-pip-version-check -w /dist . \
    && python -m venv /opt/radar \
    && /opt/radar/bin/pip install --no-cache-dir --no-deps --disable-pip-version-check -r requirements.lock '/dist/radar-0.0.0-py3-none-any.whl[test]' \
    && /opt/radar/bin/pip check --disable-pip-version-check
COPY contracts/examples/ /check/contracts/examples/
COPY contracts/capabilities.json /check/contracts/
COPY tests/test_contracts.py /check/tests/
COPY tests/domain/ /check/tests/domain/
WORKDIR /check
ENV HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
RUN --network=none /opt/radar/bin/python -c "import radar.domain.core as c; assert '/opt/radar/' in c.__file__, c.__file__" \
    && /opt/radar/bin/python -m pytest -q -p no:cacheprovider tests/domain tests/test_contracts.py
USER nobody
CMD ["/opt/radar/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/domain", "tests/test_contracts.py"]
