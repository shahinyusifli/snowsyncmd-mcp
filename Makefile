VERSION := $(shell grep '^version' pyproject.toml | cut -d'"' -f2)

.PHONY: build publish tag release clean

build:
	pip install hatch --quiet
	hatch build

publish: build
	pip install twine --quiet
	twine upload dist/*

tag:
	git tag v$(VERSION)
	git push origin v$(VERSION)

release: tag
	@echo "Go to https://github.com/shahinyusifli/snowsyncmd-mcp/releases/new"
	@echo "Select tag v$(VERSION) and publish — GitHub Actions will push to PyPI automatically."

clean:
	rm -rf dist/ *.egg-info
