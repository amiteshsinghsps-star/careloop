PY      ?= python
PORT    ?= 8080
HOST    ?= 127.0.0.1
AUTH    ?= off
# extra flags for `make preflight`, e.g. make preflight PREFLIGHT_FLAGS=--skip-auth --json
PREFLIGHT_FLAGS ?=

.PHONY: help install test serve dev dist build sync demo eval preflight smoke addon clean \
        dev-site dev-sim dev-companion chaos bundle budgets citations

CHAOS ?= latency_800ms,db_locked

help:
	@echo "make install        pip install -r requirements.txt && npm install"
	@echo "make dist           build the three frontends and copy them into careloop/web"
	@echo "make serve          run the CareLoop server (MCP + companion + simulator + judge)"
	@echo "make demo           build + serve with auth off, seeded demo household"
	@echo "make test           python tests (protocol, oauth, policy, store, tools)"
	@echo "make eval           extraction eval + dose-leak and hallucination report"
	@echo "make budgets        build+sync, then check bundle sizes, self-contained card, no .env/secrets"
	@echo "make smoke          one-shot MCP handshake + tools/list + a tool call"
	@echo "make preflight      run alexa-mcp-preflight against the local server"
	@echo "make dev-sim        svelte dev server (5173)   / dev-companion (4200) / dev-site (4321)"
	@echo "make chaos          serve with injected failures (game day)"
	@echo "make bundle         build + verify the offline demo bundle"

install:
	$(PY) -m pip install -r requirements.txt
	npm install

build:
	npm run build

sync:
	npm run sync

dist: build sync

serve:
	CARELOOP_AUTH=$(AUTH) HOST=$(HOST) PORT=$(PORT) $(PY) -m careloop.mcp_server

demo: dist
	CARELOOP_AUTH=off CARELOOP_DEMO=1 HOST=$(HOST) PORT=$(PORT) $(PY) -m careloop.mcp_server

dev-sim:
	npm run dev -w frontend/simulator

dev-companion:
	npm run dev -w frontend/companion

dev-site:
	npm run dev -w frontend/site

test:
	$(PY) -m pytest -q tests

eval:
	$(PY) -m scripts.eval.run

budgets: dist
	$(PY) scripts/check_budgets.py

citations:
	$(PY) scripts/check_citations.py

smoke:
	$(PY) scripts/smoke.py http://127.0.0.1:$(PORT)/mcp

preflight:
	$(PY) -m preflight http://127.0.0.1:$(PORT)/mcp $(PREFLIGHT_FLAGS)

chaos:
	CARELOOP_AUTH=off CARELOOP_CHAOS=$(CHAOS) $(PY) -m careloop.mcp_server

bundle:
	$(PY) scripts/offline_bundle.py make
	$(PY) scripts/offline_bundle.py verify

addon:
	$(PY) scripts/make_addon.py

clean:
	rm -rf frontend/simulator/dist frontend/simulator/dist-card frontend/companion/dist frontend/site/dist \
	       careloop/web/site careloop/web/simulator careloop/web/companion data
