FROM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS build
WORKDIR /src
COPY lab/sessions/go.mod lab/sessions/go.sum ./
RUN go mod download && go mod verify
COPY lab/sessions/*.go ./
RUN go test -mod=readonly ./... && CGO_ENABLED=0 go build -mod=readonly -trimpath -ldflags="-s -w" -o /sessionlab .

FROM scratch
COPY --from=build /sessionlab /sessionlab
USER 65532:65532
ENTRYPOINT ["/sessionlab"]
CMD ["selftest"]
