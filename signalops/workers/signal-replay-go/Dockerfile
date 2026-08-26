FROM golang:1.24-alpine AS test
WORKDIR /src
COPY workers/signal-replay-go/go.mod ./
RUN go mod download
COPY workers/signal-replay-go/ ./
RUN go test ./...

FROM test AS builder
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /signal-replay .

FROM gcr.io/distroless/static-debian12:nonroot AS runtime
COPY --from=builder /signal-replay /signal-replay
USER nonroot:nonroot
ENTRYPOINT ["/signal-replay"]
