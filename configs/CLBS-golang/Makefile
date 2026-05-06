BINARY  := proxmox-drs
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
LDFLAGS := -ldflags "-s -w -X main.version=$(VERSION)"

.PHONY: build run tidy lint clean

build:
	go build $(LDFLAGS) -o $(BINARY) .

run: build
	./$(BINARY)

tidy:
	go mod tidy

lint:
	golangci-lint run ./...

clean:
	rm -f $(BINARY)
