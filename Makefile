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
	@echo "Tag v$(VERSION) pushed."
	@echo "Create the release at: https://github.com/shahinyusifli/snowsyncmd-mcp/releases/new"

clean:
	rm -rf dist/ *.egg-info
