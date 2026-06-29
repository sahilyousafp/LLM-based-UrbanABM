REGISTRY := sahilyousafp
TAG      := latest

.PHONY: build push up up-ollama down logs

build:
	docker compose build

push: build
	docker push $(REGISTRY)/urban-abm-backend:$(TAG)
	docker push $(REGISTRY)/urban-abm-frontend:$(TAG)

up:
	docker compose up

up-ollama:
	docker compose --profile ollama up

down:
	docker compose down

logs:
	docker compose logs -f
