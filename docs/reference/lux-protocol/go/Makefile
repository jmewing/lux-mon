BINARY := lux
OUTDIR := bin

.PHONY: build test clean fmt vet lint

build:
	CGO_ENABLED=0 go build -o $(OUTDIR)/$(BINARY) .

test:
	CGO_ENABLED=0 go test ./...

clean:
	rm -rf $(OUTDIR)

fmt:
	go fmt ./...

vet:
	go vet ./...

lint:
	golangci-lint run ./...
