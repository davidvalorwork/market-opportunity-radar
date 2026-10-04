# Build context = repo root. Succeeds only if Python, Node and Go accept/reject the same golden examples.
# docker build -f contracts/Dockerfile -t market-radar/b1-contracts:test .
FROM python:3.11.17-slim@sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51 AS python
WORKDIR /repo
COPY pyproject.toml requirements.lock LICENSE NOTICE ./
COPY src/ src/
# Build isolation fetches the setuptools version pinned in pyproject.toml.
RUN pip wheel --no-cache-dir --no-deps --disable-pip-version-check -w /dist . \
    && python -m venv /opt/radar \
    && /opt/radar/bin/pip install --no-cache-dir --no-deps --disable-pip-version-check -r requirements.lock /dist/radar-0.0.0-py3-none-any.whl
# Run against the INSTALLED wheel from a directory without src/ or pyproject.toml.
COPY contracts/examples/ /check/contracts/examples/
COPY contracts/capabilities.json /check/contracts/
COPY tests/test_contracts.py /check/tests/
WORKDIR /check
RUN --network=none /opt/radar/bin/python -c "import radar.contracts as c; assert '/opt/radar/' in c.__file__, c.__file__; print('radar from', c.__file__)" \
    && /opt/radar/bin/python -m pytest -q -p no:cacheprovider tests/test_contracts.py && touch /python.ok

FROM node:24.21.0-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS node
WORKDIR /repo/contracts/validate/node
COPY contracts/validate/node/package.json contracts/validate/node/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY src/radar/schemas/ /repo/src/radar/schemas/
COPY contracts/ /repo/contracts/
RUN --network=none node validate.mjs && touch /node.ok

FROM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS go
ENV CGO_ENABLED=0
WORKDIR /repo/contracts/validate/go
COPY contracts/validate/go/go.mod contracts/validate/go/go.sum ./
RUN go mod download && go mod verify
COPY src/radar/schemas/ /repo/src/radar/schemas/
COPY contracts/ /repo/contracts/
RUN --network=none go test -mod=readonly -v ./... && touch /go.ok

FROM scratch
COPY --from=python /python.ok /checks/
COPY --from=node /node.ok /checks/
COPY --from=go /go.ok /checks/
