# Stage 1 developer entrypoints. Every target is safe to run from a clean clone.
PROJECT   ?= filing-facts-gb
REGION    ?= europe-west2
REPO      ?= filing-facts
IMAGE_NAME = $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)/ingest
TAG       ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
IMAGE      = $(IMAGE_NAME):$(TAG)

.PHONY: help sync lint typecheck test check secrets cost run-local docker-build docker-run push \
        tf-fmt tf-validate bootstrap-init bootstrap-apply init plan apply destroy

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

sync: ## Create the virtualenv from uv.lock
	uv sync --frozen

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Pyright strict
	uv run pyright

test: ## pytest
	uv run pytest

secrets: ## gitleaks over the full git history
	gitleaks git --no-banner .

check: lint typecheck test tf-fmt tf-validate ## Everything CI runs, minus gitleaks

cost: ## Regenerate docs/cost.md from measured inputs
	uv run python scripts/estimate_cost.py > docs/cost.md
	@echo wrote docs/cost.md

run-local: ## Run the job against local filesystem backends (no GCP needed)
	uv run python -m filing_facts.ingest --dry-run $(ARGS)

docker-build: ## Build the amd64 image Cloud Run needs
	docker build --platform linux/amd64 -t $(IMAGE) -t $(IMAGE_NAME):latest .

docker-run: docker-build ## Run the container locally in dry-run mode, output under ./data
	docker run --rm --platform linux/amd64 -v "$(PWD)/data:/app/data" $(IMAGE) --dry-run $(ARGS)

push: docker-build ## Push to Artifact Registry (needs: gcloud auth configure-docker $(REGION)-docker.pkg.dev)
	docker push $(IMAGE)
	docker push $(IMAGE_NAME):latest
	@echo "image=$(IMAGE)"

tf-fmt:
	terraform -chdir=infra/bootstrap fmt -check -recursive
	terraform -chdir=infra/stage1 fmt -check -recursive

tf-validate:
	terraform -chdir=infra/bootstrap init -backend=false -input=false >/dev/null && terraform -chdir=infra/bootstrap validate
	terraform -chdir=infra/stage1 init -backend=false -input=false >/dev/null && terraform -chdir=infra/stage1 validate

bootstrap-init: ## Bootstrap root uses local state (it creates the remote-state bucket)
	terraform -chdir=infra/bootstrap init -input=false

bootstrap-apply: bootstrap-init ## Enable APIs, create state bucket + Artifact Registry
	terraform -chdir=infra/bootstrap apply -var="project_id=$(PROJECT)"

init: ## Stage 1 root, remote state in the bootstrap bucket
	terraform -chdir=infra/stage1 init -input=false -backend-config="bucket=$(PROJECT)-tfstate"

plan: init
	terraform -chdir=infra/stage1 plan -var="project_id=$(PROJECT)" -var="image=$(IMAGE)"

apply: init ## Provision everything Stage 1 needs. Run `make push` first so the image exists.
	terraform -chdir=infra/stage1 apply -var="project_id=$(PROJECT)" -var="image=$(IMAGE)"

destroy: init ## Tear down Stage 1 (raw bucket included, force_destroy=true)
	terraform -chdir=infra/stage1 destroy -var="project_id=$(PROJECT)" -var="image=$(IMAGE)"
