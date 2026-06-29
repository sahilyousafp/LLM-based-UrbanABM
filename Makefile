REGISTRY := sahilyousafp
TAG      := latest
PLATFORMS := linux/amd64,linux/arm64

.PHONY: build push push-cross up up-ollama down logs reset-db

build:
	docker compose build

push: build
	docker push $(REGISTRY)/urban-abm-backend:$(TAG)
	docker push $(REGISTRY)/urban-abm-frontend:$(TAG)

# Multi-platform push (required when building on Apple Silicon for x86 cloud hosts).
# Requires: docker buildx create --use
push-cross:
	docker buildx build --platform $(PLATFORMS) \
		-t $(REGISTRY)/urban-abm-backend:$(TAG) --push .
	docker buildx build --platform $(PLATFORMS) \
		-t $(REGISTRY)/urban-abm-frontend:$(TAG) --push \
		-f Frontend/Dockerfile .

up:
	docker compose up

up-ollama:
	docker compose --profile ollama up

down:
	docker compose down

logs:
	docker compose logs -f

# Wipe the environment volume so the next `up` re-seeds DuckDB files from the image.
# Run this after rebuilding the image with updated spatial databases.
reset-db:
	docker compose down
	docker volume rm $$(docker volume ls -q | grep abm_environment) 2>/dev/null || true
	docker compose up
