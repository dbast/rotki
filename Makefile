pre-commit:
	pre-commit run --verbose --color=always --all-files --show-diff-on-failure


pre-commit-fast:
	pre-commit run --verbose --color=always --all-files ruff
	pre-commit run --verbose --color=always --all-files mypy


clean:
	rm -rf build/ dist/ rotkehlchen_py_dist/ htmlcov/ rotkehlchen.egg-info/ *.dmg frontend/app/dist/ frontend/app/build/


docker-image:
	packaging/docker-image.sh


test-assets:
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_binance.py::test_binance_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_binance_us.py::test_binance_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_bitfinex.py::test_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_bitstamp.py::test_bitstamp_exchange_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_coinbase.py::test_coverage_of_products
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_kraken.py::test_coverage_of_kraken_balances
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_iconomi.py::test_iconomi_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_kraken.py::test_kraken_to_world_pair
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_kucoin.py::test_kucoin_exchange_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_poloniex.py::test_poloniex_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_gemini.py::test_gemini_all_symbols_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_okx.py::test_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/unit/test_assets.py::test_coingecko_identifiers_are_reachable
	python pytestgeventwrapper.py rotkehlchen/tests/unit/test_assets.py::test_cryptocompare_asset_support
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_independentreserve.py::test_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/unit/test_zerionsdk.py::test_protocol_names_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/unit/test_zerionsdk.py::test_query_all_protocol_balances_for_account
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_woo.py::test_woo_assets_are_known
	python pytestgeventwrapper.py rotkehlchen/tests/exchanges/test_bybit.py::test_assets_are_known

create-cassettes:
	RECORD_CASSETTES=true python pytestgeventwrapper.py -m vcr rotkehlchen/tests

create-cassette:
	RECORD_CASSETTES=true python pytestgeventwrapper.py -m vcr $(filter-out $@,$(MAKECMDGOALS))



# A macro to catch extra makefile arguments and use them elsewhere
# https://stackoverflow.com/a/6273809/110395
%:
	@:

.PHONY: all $(MAKECMDGOALS)
