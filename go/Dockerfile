# Build context: repository root. Same pinned toolchain as lab/sessions.
# The repo layout is kept under /repo so tests find src/radar/schemas and contracts/examples.
FROM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS build
# Pure-Go SQLite (modernc.org/sqlite): no C toolchain, static binaries; never fetch another toolchain.
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /repo/go
COPY go/go.mod go/go.sum ./
# Only step with network: fill and verify the module cache against go.sum.
RUN go mod download && go mod verify
COPY src/radar/schemas/ /repo/src/radar/schemas/
COPY contracts/examples/ /repo/contracts/examples/
COPY go/ ./
# Offline from here: vet, tests and build use only the verified module cache.
RUN --network=none go vet -mod=readonly ./... \
 && go test -mod=readonly -count=1 ./... \
 && go build -mod=readonly -trimpath -ldflags="-s -w" -o /out/ ./cmd/sessions ./cmd/whatsapp

FROM scratch
# TLS roots for the gated --real mode (wss://web.whatsapp.com); unused by selftest and --fake.
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/sessions /sessions
COPY --from=build /out/whatsapp /whatsapp
USER 65532:65532
CMD ["/sessions", "selftest"]
