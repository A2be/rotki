from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from flask import Blueprint, Request, Response, request as flask_request
from flask_restful import Resource
from marshmallow import Schema
from marshmallow.utils import missing
from webargs.flaskparser import parser, use_kwargs
from webargs.multidictproxy import MultiDictProxy
from werkzeug.datastructures import FileStorage

from rotkehlchen.accounting.ledger_actions import LedgerAction
from rotkehlchen.accounting.structures.base import ActionType, HistoryBaseEntry
from rotkehlchen.api.rest import RestAPI
from rotkehlchen.api.v1.parser import ignore_kwarg_parser, resource_parser
from rotkehlchen.api.v1.schemas import (
    AccountingReportDataSchema,
    AccountingReportsSchema,
    AllBalancesQuerySchema,
    AppInfoSchema,
    AssetIconUploadSchema,
    AssetMovementsQuerySchema,
    AssetResetRequestSchema,
    AssetSchema,
    AssetsImportingFromFormSchema,
    AssetsImportingSchema,
    AssetsReplaceSchema,
    AssetUpdatesRequestSchema,
    AsyncHistoricalQuerySchema,
    AsyncIgnoreCacheQueryArgumentSchema,
    AsyncQueryArgumentSchema,
    AsyncTasksQuerySchema,
    AvalancheTransactionQuerySchema,
    BaseXpubSchema,
    BinanceMarketsSchema,
    BinanceMarketsUserSchema,
    BlockchainAccountsDeleteSchema,
    BlockchainAccountsGetSchema,
    BlockchainAccountsPatchSchema,
    BlockchainAccountsPutSchema,
    BlockchainBalanceQuerySchema,
    CurrentAssetsPriceSchema,
    DataImportSchema,
    EditSettingsSchema,
    ERC20InfoSchema,
    Eth2DailyStatsSchema,
    Eth2ValidatorDeleteSchema,
    Eth2ValidatorPatchSchema,
    Eth2ValidatorPutSchema,
    EthereumTransactionDecodingSchema,
    EthereumTransactionQuerySchema,
    ExchangeBalanceQuerySchema,
    ExchangeRatesSchema,
    ExchangesDataResourceSchema,
    ExchangesResourceAddSchema,
    ExchangesResourceEditSchema,
    ExchangesResourceRemoveSchema,
    ExternalServicesResourceAddSchema,
    ExternalServicesResourceDeleteSchema,
    FileListSchema,
    HistoricalAssetsPriceSchema,
    HistoryBaseEntrySchema,
    HistoryExportingSchema,
    HistoryProcessingSchema,
    IdentifiersListSchema,
    IgnoredActionsGetSchema,
    IgnoredActionsModifySchema,
    IgnoredAssetsSchema,
    IntegerIdentifierSchema,
    LedgerActionSchema,
    LedgerActionsQuerySchema,
    ManuallyTrackedBalancesDeleteSchema,
    ManuallyTrackedBalancesSchema,
    ManualPriceDeleteSchema,
    ManualPriceRegisteredSchema,
    ManualPriceSchema,
    ModifyEthereumTokenSchema,
    NameDeleteSchema,
    NamedEthereumModuleDataSchema,
    NamedOracleCacheCreateSchema,
    NamedOracleCacheGetSchema,
    NamedOracleCacheSchema,
    NewUserSchema,
    OptionalEthereumAddressSchema,
    QueriedAddressesSchema,
    RequiredEthereumAddressSchema,
    ReverseEnsSchema,
    SingleAssetIdentifierSchema,
    SingleFileSchema,
    SnapshotExportingSchema,
    SnapshotImportingSchema,
    SnapshotTimestampQuerySchema,
    StakingQuerySchema,
    StatisticsAssetBalanceSchema,
    StatisticsNetValueSchema,
    StatisticsValueDistributionSchema,
    StringIdentifierSchema,
    TagSchema,
    TimedManualPriceSchema,
    TradeDeleteSchema,
    TradePatchSchema,
    TradeSchema,
    TradesQuerySchema,
    UserActionSchema,
    UserPasswordChangeSchema,
    UserPremiumSyncSchema,
    WatchersAddSchema,
    WatchersDeleteSchema,
    WatchersEditSchema,
    XpubAddSchema,
    XpubPatchSchema,
)
from rotkehlchen.assets.asset import Asset, EthereumToken
from rotkehlchen.assets.types import AssetType
from rotkehlchen.balances.manual import ManuallyTrackedBalance
from rotkehlchen.chain.bitcoin.xpub import XpubData
from rotkehlchen.db.filtering import (
    AssetMovementsFilterQuery,
    Eth2DailyStatsFilterQuery,
    ETHTransactionsFilterQuery,
    LedgerActionsFilterQuery,
    ReportDataFilterQuery,
)
from rotkehlchen.db.settings import ModifiableDBSettings
from rotkehlchen.fval import FVal
from rotkehlchen.history.types import HistoricalPriceOracle
from rotkehlchen.types import (
    IMPORTABLE_LOCATIONS,
    ApiKey,
    ApiSecret,
    AssetAmount,
    BlockchainAccountData,
    ChecksumEthAddress,
    Eth2PubKey,
    EVMTxHash,
    ExternalService,
    ExternalServiceApiCredentials,
    Fee,
    HexColorCode,
    ListOfBlockchainAddresses,
    Location,
    ModuleName,
    Price,
    SupportedBlockchain,
    Timestamp,
    TradeType,
)

if TYPE_CHECKING:
    from rotkehlchen.chain.bitcoin.hdkey import HDKey
    from rotkehlchen.db.filtering import HistoryEventFilterQuery
    from rotkehlchen.exchanges.kraken import KrakenAccountType


def _combine_parser_data(
        data_1: MultiDictProxy,
        data_2: MultiDictProxy,
        schema: Schema,
) -> MultiDictProxy:
    if data_2 is not missing:
        if data_1 == {}:
            data_1 = MultiDictProxy(data_2, schema)
        else:
            all_data = data_1.to_dict() if isinstance(data_1, MultiDictProxy) else data_1
            for key, value in data_2.items():
                all_data[key] = value
            data_1 = MultiDictProxy(all_data, schema)
    return data_1


@parser.location_loader('json_and_view_args')
def load_json_viewargs_data(request: Request, schema: Schema) -> Dict[str, Any]:
    """Load data from a request accepting either json or view_args encoded data"""
    view_args = parser.load_view_args(request, schema)  # type: ignore
    data = parser.load_json(request, schema)
    if data is missing:
        return data

    data = _combine_parser_data(data, view_args, schema)
    return data


@parser.location_loader('json_and_query')
def load_json_query_data(request: Request, schema: Schema) -> Dict[str, Any]:
    """Load data from a request accepting either json or query encoded data"""
    data = parser.load_json(request, schema)
    if data is not missing:
        return data
    return parser.load_querystring(request, schema)  # type: ignore


@parser.location_loader('json_and_query_and_view_args')
def load_json_query_viewargs_data(request: Request, schema: Schema) -> Dict[str, Any]:
    """Load data from a request accepting either json or querystring or view_args encoded data"""
    view_args = parser.load_view_args(request, schema)  # type: ignore
    # Get data either from json or from querystring
    data = parser.load_json(request, schema)
    if data is missing:
        data = parser.load_querystring(request, schema)  # type: ignore

    if data is missing:
        return data

    data = _combine_parser_data(data, view_args, schema)
    return data


@parser.location_loader('form_and_file')
def load_form_file_data(request: Request, schema: Schema) -> MultiDictProxy:
    """Load data from a request accepting form and file encoded data"""
    form_data = parser.load_form(request, schema)  # type: ignore
    file_data = parser.load_files(request, schema)  # type: ignore
    data = _combine_parser_data(form_data, file_data, schema)
    return data


@parser.location_loader('view_args_and_file')
def load_view_args_file_data(request: Request, schema: Schema) -> MultiDictProxy:
    """Load data from a request accepting view_args and file encoded data"""
    view_args_data = parser.load_view_args(request, schema)  # type: ignore
    file_data = parser.load_files(request, schema)  # type: ignore
    data = _combine_parser_data(view_args_data, file_data, schema)
    return data


def create_blueprint() -> Blueprint:
    # Take a look at this SO question on hints how to organize versioned
    # API with flask:
    # http://stackoverflow.com/questions/28795561/support-multiple-api-versions-in-flask#28797512
    return Blueprint("v1_resources", __name__)


class BaseResource(Resource):
    def __init__(self, rest_api_object: RestAPI, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rest_api = rest_api_object


class SettingsResource(BaseResource):

    put_schema = EditSettingsSchema()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            settings: ModifiableDBSettings,
    ) -> Response:
        return self.rest_api.set_settings(settings)

    def get(self) -> Response:
        return self.rest_api.get_settings()


class AsyncTasksResource(BaseResource):

    get_schema = AsyncTasksQuerySchema()

    @use_kwargs(get_schema, location='view_args')
    def get(self, task_id: Optional[int]) -> Response:
        return self.rest_api.query_tasks_outcome(task_id=task_id)


class ExchangeRatesResource(BaseResource):

    get_schema = ExchangeRatesSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, currencies: List[Optional[Asset]], async_query: bool) -> Response:
        valid_currencies = [currency for currency in currencies if currency is not None]
        return self.rest_api.get_exchange_rates(given_currencies=valid_currencies, async_query=async_query)  # noqa: E501


class ExchangesResource(BaseResource):

    put_schema = ExchangesResourceAddSchema()
    patch_schema = ExchangesResourceEditSchema()
    delete_schema = ExchangesResourceRemoveSchema()

    def get(self) -> Response:
        return self.rest_api.get_exchanges()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            name: str,
            location: Location,
            api_key: ApiKey,
            api_secret: ApiSecret,
            passphrase: Optional[str],
            kraken_account_type: Optional['KrakenAccountType'],
            binance_markets: Optional[List[str]],
            ftx_subaccount: Optional[str],
    ) -> Response:
        return self.rest_api.setup_exchange(
            name=name,
            location=location,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            kraken_account_type=kraken_account_type,
            binance_markets=binance_markets,
            ftx_subaccount=ftx_subaccount,
        )

    @use_kwargs(patch_schema, location='json')
    def patch(
            self,
            name: str,
            location: Location,
            new_name: Optional[str],
            api_key: Optional[ApiKey],
            api_secret: Optional[ApiSecret],
            passphrase: Optional[str],
            kraken_account_type: Optional['KrakenAccountType'],
            binance_markets: Optional[List[str]],
            ftx_subaccount: Optional[str],
    ) -> Response:
        return self.rest_api.edit_exchange(
            name=name,
            location=location,
            new_name=new_name,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            kraken_account_type=kraken_account_type,
            binance_markets=binance_markets,
            ftx_subaccount=ftx_subaccount,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(self, name: str, location: Location) -> Response:
        return self.rest_api.remove_exchange(name=name, location=location)


class ExchangesDataResource(BaseResource):

    delete_schema = ExchangesDataResourceSchema()

    @use_kwargs(delete_schema, location='view_args')
    def delete(self, location: Optional[Location]) -> Response:
        return self.rest_api.purge_exchange_data(location=location)


class AssociatedLocations(BaseResource):
    def get(self) -> Response:
        return self.rest_api.get_associated_locations()


class EthereumTransactionsResource(BaseResource):
    get_schema = EthereumTransactionQuerySchema()
    post_schema = EthereumTransactionDecodingSchema()

    @ignore_kwarg_parser.use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(
            self,
            async_query: bool,
            only_cache: bool,
            filter_query: ETHTransactionsFilterQuery,
    ) -> Response:
        return self.rest_api.get_ethereum_transactions(
            async_query=async_query,
            only_cache=only_cache,
            filter_query=filter_query,
        )

    @use_kwargs(post_schema, location='json_and_query')
    def post(
            self,
            async_query: bool,
            ignore_cache: bool,
            tx_hashes: List[EVMTxHash],
    ) -> Response:
        return self.rest_api.decode_ethereum_transactions(
            async_query=async_query,
            ignore_cache=ignore_cache,
            tx_hashes=tx_hashes,
        )

    def delete(self) -> Response:
        return self.rest_api.purge_ethereum_transaction_data()


class EthereumAirdropsResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_ethereum_airdrops(async_query)


class ExternalServicesResource(BaseResource):

    put_schema = ExternalServicesResourceAddSchema()
    delete_schema = ExternalServicesResourceDeleteSchema()

    def get(self) -> Response:
        return self.rest_api.get_external_services()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            services: List[ExternalServiceApiCredentials],
    ) -> Response:
        return self.rest_api.add_external_services(services=services)

    @use_kwargs(delete_schema, location='json')
    def delete(self, services: List[ExternalService]) -> Response:
        return self.rest_api.delete_external_services(services=services)


class AllBalancesResource(BaseResource):

    get_schema = AllBalancesQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            save_data: bool,
            ignore_errors: bool,
            async_query: bool,
            ignore_cache: bool,
    ) -> Response:
        return self.rest_api.query_all_balances(
            save_data=save_data,
            ignore_errors=ignore_errors,
            async_query=async_query,
            ignore_cache=ignore_cache,
        )


class ExchangeBalancesResource(BaseResource):

    get_schema = ExchangeBalanceQuerySchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(self, location: Optional[Location], async_query: bool, ignore_cache: bool) -> Response:
        return self.rest_api.query_exchange_balances(
            location=location,
            async_query=async_query,
            ignore_cache=ignore_cache,
        )


class OwnedAssetsResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.query_owned_assets()


class DatabaseInfoResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.get_database_info()


class DatabaseBackupsResource(BaseResource):

    delete_schema = FileListSchema()
    get_schema = SingleFileSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, file: Path) -> Response:
        return self.rest_api.download_database_backup(filepath=file)

    def put(self) -> Response:
        return self.rest_api.create_database_backup()

    @use_kwargs(delete_schema, location='json')
    def delete(self, files: List[Path]) -> Response:
        return self.rest_api.delete_database_backups(files=files)


class AllAssetsResource(BaseResource):

    delete_schema = StringIdentifierSchema()

    def make_add_schema(self) -> AssetSchema:
        return AssetSchema(
            identifier_required=False,
            coingecko=self.rest_api.rotkehlchen.coingecko,
            cryptocompare=self.rest_api.rotkehlchen.cryptocompare,
        )

    def make_edit_schema(self) -> AssetSchema:
        return AssetSchema(
            identifier_required=True,
            coingecko=self.rest_api.rotkehlchen.coingecko,
            cryptocompare=self.rest_api.rotkehlchen.cryptocompare,
        )

    def get(self) -> Response:
        return self.rest_api.query_all_assets()

    @resource_parser.use_kwargs(make_add_schema, location='json')
    def put(self, asset_type: AssetType, **kwargs: Any) -> Response:
        return self.rest_api.add_custom_asset(asset_type, **kwargs)

    @resource_parser.use_kwargs(make_edit_schema, location='json')
    def patch(self, **kwargs: Any) -> Response:
        return self.rest_api.edit_custom_asset(kwargs)

    @use_kwargs(delete_schema, location='json')
    def delete(self, identifier: str) -> Response:
        return self.rest_api.delete_custom_asset(identifier)


class AssetsTypesResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.get_asset_types()


class AssetsReplaceResource(BaseResource):

    put_schema = AssetsReplaceSchema()

    @use_kwargs(put_schema, location='json')
    def put(self, source_identifier: str, target_asset: Asset) -> Response:
        return self.rest_api.replace_asset(source_identifier, target_asset)


class EthereumAssetsResource(BaseResource):

    get_schema = OptionalEthereumAddressSchema()
    delete_schema = RequiredEthereumAddressSchema()

    def make_edit_schema(self) -> ModifyEthereumTokenSchema:
        return ModifyEthereumTokenSchema(
            coingecko=self.rest_api.rotkehlchen.coingecko,
            cryptocompare=self.rest_api.rotkehlchen.cryptocompare,
        )

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, address: Optional[ChecksumEthAddress]) -> Response:
        return self.rest_api.get_custom_ethereum_tokens(address=address)

    @resource_parser.use_kwargs(make_edit_schema, location='json')
    def put(self, token: EthereumToken) -> Response:
        return self.rest_api.add_custom_ethereum_token(token=token)

    @resource_parser.use_kwargs(make_edit_schema, location='json')
    def patch(self, token: EthereumToken) -> Response:
        return self.rest_api.edit_custom_ethereum_token(token=token)

    @use_kwargs(delete_schema, location='json')
    def delete(self, address: ChecksumEthAddress) -> Response:
        return self.rest_api.delete_custom_ethereum_token(address)


class AssetUpdatesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()
    post_schema = AssetUpdatesRequestSchema()
    delete_schema = AssetResetRequestSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_assets_updates(async_query)

    @use_kwargs(post_schema, location='json')
    def post(
            self,
            async_query: bool,
            up_to_version: Optional[int],
            conflicts: Optional[Dict[Asset, Literal['remote', 'local']]],
    ) -> Response:
        return self.rest_api.perform_assets_updates(async_query, up_to_version, conflicts)

    @use_kwargs(delete_schema, location='json_and_query')
    def delete(self, reset: Literal['soft', 'hard'], ignore_warnings: bool) -> Response:
        return self.rest_api.rebuild_assets_information(reset, ignore_warnings)


class BlockchainBalancesResource(BaseResource):

    get_schema = BlockchainBalanceQuerySchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(
            self,
            blockchain: Optional[SupportedBlockchain],
            async_query: bool,
            ignore_cache: bool,
    ) -> Response:
        return self.rest_api.query_blockchain_balances(
            blockchain=blockchain,
            async_query=async_query,
            ignore_cache=ignore_cache,
        )


class ManuallyTrackedBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()
    edit_schema = ManuallyTrackedBalancesSchema()
    delete_schema = ManuallyTrackedBalancesDeleteSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_manually_tracked_balances(async_query)

    @use_kwargs(edit_schema, location='json')
    def put(self, async_query: bool, balances: List[ManuallyTrackedBalance]) -> Response:
        return self.rest_api.add_manually_tracked_balances(async_query=async_query, data=balances)

    @use_kwargs(edit_schema, location='json')
    def patch(self, async_query: bool, balances: List[ManuallyTrackedBalance]) -> Response:
        return self.rest_api.edit_manually_tracked_balances(async_query=async_query, data=balances)

    @use_kwargs(delete_schema, location='json')
    def delete(self, async_query: bool, labels: List[str]) -> Response:
        return self.rest_api.remove_manually_tracked_balances(
            async_query=async_query,
            labels=labels,
        )


class TradesResource(BaseResource):

    get_schema = TradesQuerySchema()
    put_schema = TradeSchema()
    patch_schema = TradePatchSchema()
    delete_schema = TradeDeleteSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            only_cache: bool,
            filter_query: TradesQuerySchema,
    ) -> Response:
        return self.rest_api.get_trades(
            async_query=async_query,
            only_cache=only_cache,
            filter_query=filter_query,
        )

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            timestamp: Timestamp,
            location: Location,
            base_asset: Asset,
            quote_asset: Asset,
            trade_type: TradeType,
            amount: AssetAmount,
            rate: Price,
            fee: Optional[Fee],
            fee_currency: Optional[Asset],
            link: Optional[str],
            notes: Optional[str],
    ) -> Response:
        return self.rest_api.add_trade(
            timestamp=timestamp,
            location=location,
            base_asset=base_asset,
            quote_asset=quote_asset,
            trade_type=trade_type,
            amount=amount,
            rate=rate,
            fee=fee,
            fee_currency=fee_currency,
            link=link,
            notes=notes,
        )

    @use_kwargs(patch_schema, location='json')
    def patch(
            self,
            trade_id: str,
            timestamp: Timestamp,
            location: Location,
            base_asset: Asset,
            quote_asset: Asset,
            trade_type: TradeType,
            amount: AssetAmount,
            rate: Price,
            fee: Optional[Fee],
            fee_currency: Optional[Asset],
            link: Optional[str],
            notes: Optional[str],
    ) -> Response:
        return self.rest_api.edit_trade(
            trade_id=trade_id,
            timestamp=timestamp,
            location=location,
            base_asset=base_asset,
            quote_asset=quote_asset,
            trade_type=trade_type,
            amount=amount,
            rate=rate,
            fee=fee,
            fee_currency=fee_currency,
            link=link,
            notes=notes,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(self, trade_id: str) -> Response:
        return self.rest_api.delete_trade(trade_id=trade_id)


class AssetMovementsResource(BaseResource):

    get_schema = AssetMovementsQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            filter_query: AssetMovementsFilterQuery,
            async_query: bool,
            only_cache: bool,
    ) -> Response:
        return self.rest_api.get_asset_movements(
            filter_query=filter_query,
            async_query=async_query,
            only_cache=only_cache,
        )


class TagsResource(BaseResource):

    put_schema = TagSchema(color_required=True)
    patch_schema = TagSchema(color_required=False)
    delete_schema = NameDeleteSchema()

    def get(self) -> Response:
        return self.rest_api.get_tags()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            name: str,
            description: Optional[str],
            background_color: HexColorCode,
            foreground_color: HexColorCode,
    ) -> Response:
        return self.rest_api.add_tag(
            name=name,
            description=description,
            background_color=background_color,
            foreground_color=foreground_color,
        )

    @use_kwargs(patch_schema, location='json')
    def patch(
            self,
            name: str,
            description: Optional[str],
            background_color: Optional[HexColorCode],
            foreground_color: Optional[HexColorCode],
    ) -> Response:
        return self.rest_api.edit_tag(
            name=name,
            description=description,
            background_color=background_color,
            foreground_color=foreground_color,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(self, name: str) -> Response:
        return self.rest_api.delete_tag(name=name)


class LedgerActionsResource(BaseResource):

    get_schema = LedgerActionsQuerySchema()
    put_schema = LedgerActionSchema(identifier_required=False)
    patch_schema = LedgerActionSchema(identifier_required=True)
    delete_schema = IntegerIdentifierSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            filter_query: LedgerActionsFilterQuery,
            async_query: bool,
            only_cache: bool,
    ) -> Response:
        return self.rest_api.get_ledger_actions(
            filter_query=filter_query,
            async_query=async_query,
            only_cache=only_cache,
        )

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            action: LedgerAction,
    ) -> Response:
        return self.rest_api.add_ledger_action(action)

    @use_kwargs(patch_schema, location='json')
    def patch(self, action: LedgerAction) -> Response:
        return self.rest_api.edit_ledger_action(action=action)

    @use_kwargs(delete_schema, location='json')
    def delete(self, identifier: int) -> Response:
        return self.rest_api.delete_ledger_action(identifier=identifier)


class HistoryBaseEntryResource(BaseResource):

    put_schema = HistoryBaseEntrySchema(identifier_required=False)
    patch_schema = HistoryBaseEntrySchema(identifier_required=True)
    delete_schema = IdentifiersListSchema()

    @use_kwargs(put_schema, location='json')
    def put(self, event: HistoryBaseEntry) -> Response:
        return self.rest_api.add_history_event(event)

    @use_kwargs(patch_schema, location='json')
    def patch(self, event: HistoryBaseEntry) -> Response:
        return self.rest_api.edit_history_event(event=event)

    @use_kwargs(delete_schema, location='json')
    def delete(self, identifiers: List[int]) -> Response:
        return self.rest_api.delete_history_events(identifiers=identifiers)


class UsersResource(BaseResource):

    put_schema = NewUserSchema()

    def get(self) -> Response:
        return self.rest_api.get_users()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            name: str,
            password: str,
            premium_api_key: str,
            premium_api_secret: str,
            sync_database: bool,
            initial_settings: Optional[ModifiableDBSettings],
    ) -> Response:
        return self.rest_api.create_new_user(
            name=name,
            password=password,
            premium_api_key=premium_api_key,
            premium_api_secret=premium_api_secret,
            sync_database=sync_database,
            initial_settings=initial_settings,
        )


class UsersByNameResource(BaseResource):
    patch_schema = UserActionSchema()

    @use_kwargs(patch_schema, location='json_and_view_args')
    def patch(
            self,
            action: Optional[str],
            name: str,
            password: Optional[str],
            sync_approval: Literal['yes', 'no', 'unknown'],
            premium_api_key: str,
            premium_api_secret: str,
    ) -> Response:
        if action is None:
            return self.rest_api.user_set_premium_credentials(
                name=name,
                api_key=premium_api_key,
                api_secret=premium_api_secret,
            )

        if action == 'login':
            assert password is not None, 'Marshmallow validation should not let password=None here'
            return self.rest_api.user_login(
                name=name,
                password=password,
                sync_approval=sync_approval,
            )

        # else can only be logout -- checked by marshmallow
        return self.rest_api.user_logout(name=name)


class UserPasswordChangeResource(BaseResource):
    patch_schema = UserPasswordChangeSchema

    @use_kwargs(patch_schema, location='json')
    def patch(
            self,
            name: str,
            current_password: str,
            new_password: str,
    ) -> Response:
        return self.rest_api.user_change_password(
            name=name,
            current_password=current_password,
            new_password=new_password,
        )


class UserPremiumKeyResource(BaseResource):

    def delete(self) -> Response:
        return self.rest_api.user_premium_key_remove()


class UserPremiumSyncResource(BaseResource):
    put_schema = UserPremiumSyncSchema()

    @use_kwargs(put_schema, location='json_and_view_args')
    def put(self, async_query: bool, action: Literal['upload', 'download']) -> Response:
        return self.rest_api.sync_data(async_query, action)


class StatisticsNetvalueResource(BaseResource):

    get_schema = StatisticsNetValueSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, include_nfts: bool) -> Response:
        return self.rest_api.query_netvalue_data(include_nfts)


class StatisticsAssetBalanceResource(BaseResource):

    get_schema = StatisticsAssetBalanceSchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(
            self,
            asset: Asset,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.query_timed_balances_data(
            asset=asset,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class StatisticsValueDistributionResource(BaseResource):

    get_schema = StatisticsValueDistributionSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, distribution_by: str) -> Response:
        return self.rest_api.query_value_distribution_data(
            distribution_by=distribution_by,
        )


class StatisticsRendererResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.query_premium_components()


class MessagesResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.get_messages()


class HistoryStatusResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.get_history_status()


class HistoryProcessingResource(BaseResource):

    get_schema = HistoryProcessingSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
            async_query: bool,
    ) -> Response:
        return self.rest_api.process_history(
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            async_query=async_query,
        )


class HistoryActionableItemsResource(BaseResource):
    def get(self) -> Response:
        return self.rest_api.get_history_actionable_items()


class AccountingReportsResource(BaseResource):

    get_schema = AccountingReportsSchema(required_report_id=False)
    delete_schema = AccountingReportsSchema(required_report_id=True)

    @use_kwargs(get_schema, location='view_args')
    def get(self, report_id: Optional[int]) -> Response:
        return self.rest_api.get_pnl_reports(report_id=report_id)

    @use_kwargs(delete_schema, location='view_args')
    def delete(self, report_id: int) -> Response:
        return self.rest_api.purge_pnl_report_data(report_id=report_id)


class AccountingReportDataResource(BaseResource):

    post_schema = AccountingReportDataSchema()

    @ignore_kwarg_parser.use_kwargs(post_schema, location='json_and_query_and_view_args')
    def post(self, filter_query: ReportDataFilterQuery) -> Response:
        return self.rest_api.get_report_data(filter_query=filter_query)


class HistoryExportingResource(BaseResource):

    get_schema = HistoryExportingSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, directory_path: Path) -> Response:
        return self.rest_api.export_processed_history_csv(directory_path=directory_path)


class HistoryDownloadingResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.download_processed_history_csv()


class PeriodicDataResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.query_periodic_data()


class BlockchainsAccountsResource(BaseResource):

    get_schema = BlockchainAccountsGetSchema()

    def make_put_schema(self) -> BlockchainAccountsPutSchema:
        return BlockchainAccountsPutSchema(
            self.rest_api.rotkehlchen.chain_manager.ethereum,
        )

    def make_patch_schema(self) -> BlockchainAccountsPatchSchema:
        return BlockchainAccountsPatchSchema(
            self.rest_api.rotkehlchen.chain_manager.ethereum,
        )

    def make_delete_schema(self) -> BlockchainAccountsDeleteSchema:
        return BlockchainAccountsDeleteSchema(
            self.rest_api.rotkehlchen.chain_manager.ethereum,
        )

    @use_kwargs(get_schema, location='view_args')
    def get(self, blockchain: SupportedBlockchain) -> Response:
        return self.rest_api.get_blockchain_accounts(blockchain)

    @resource_parser.use_kwargs(make_put_schema, location='json_and_view_args')
    def put(
            self,
            blockchain: SupportedBlockchain,
            accounts: List[Dict[str, Any]],
            async_query: bool,
    ) -> Response:
        account_data = [
            BlockchainAccountData(
                address=entry['address'],
                label=entry['label'],
                tags=entry['tags'],
            ) for entry in accounts
        ]
        return self.rest_api.add_blockchain_accounts(
            blockchain=blockchain,
            account_data=account_data,
            async_query=async_query,
        )

    @resource_parser.use_kwargs(make_patch_schema, location='json_and_view_args')
    def patch(
            self,
            blockchain: SupportedBlockchain,
            accounts: List[Dict[str, Any]],
    ) -> Response:
        account_data = [
            BlockchainAccountData(
                address=entry['address'],
                label=entry['label'],
                tags=entry['tags'],
            ) for entry in accounts
        ]
        return self.rest_api.edit_blockchain_accounts(
            blockchain=blockchain,
            account_data=account_data,
        )

    @resource_parser.use_kwargs(make_delete_schema, location='json_and_view_args')
    def delete(
            self,
            blockchain: SupportedBlockchain,
            accounts: ListOfBlockchainAddresses,
            async_query: bool,
    ) -> Response:
        return self.rest_api.remove_blockchain_accounts(
            blockchain=blockchain,
            accounts=accounts,
            async_query=async_query,
        )


class BTCXpubResource(BaseResource):

    put_schema = XpubAddSchema()
    delete_schema = BaseXpubSchema()
    patch_schema = XpubPatchSchema()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            xpub: 'HDKey',
            derivation_path: Optional[str],
            label: Optional[str],
            tags: Optional[List[str]],
            async_query: bool,
    ) -> Response:
        return self.rest_api.add_xpub(
            xpub_data=XpubData(
                xpub=xpub,
                derivation_path=derivation_path,
                label=label,
                tags=tags,
            ),
            async_query=async_query,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(
            self,
            xpub: 'HDKey',
            derivation_path: Optional[str],
            async_query: bool,
    ) -> Response:
        return self.rest_api.delete_xpub(
            xpub_data=XpubData(
                xpub=xpub,
                derivation_path=derivation_path,
                label=None,
                tags=None,
            ),
            async_query=async_query,
        )

    @use_kwargs(patch_schema, location='json_and_view_args')
    def patch(
            self,
            xpub: 'HDKey',
            derivation_path: Optional[str],
            label: Optional[str],
            tags: Optional[List[str]],
    ) -> Response:
        return self.rest_api.edit_xpub(
            xpub_data=XpubData(
                xpub=xpub,
                derivation_path=derivation_path,
                label=label,
                tags=tags,
            ),
        )


class IgnoredAssetsResource(BaseResource):

    modify_schema = IgnoredAssetsSchema()

    def get(self) -> Response:
        return self.rest_api.get_ignored_assets()

    @use_kwargs(modify_schema, location='json')
    def put(self, assets: List[Asset]) -> Response:
        return self.rest_api.add_ignored_assets(assets=assets)

    @use_kwargs(modify_schema, location='json')
    def delete(self, assets: List[Asset]) -> Response:
        return self.rest_api.remove_ignored_assets(assets=assets)


class IgnoredActionsResource(BaseResource):

    get_schema = IgnoredActionsGetSchema()
    modify_schema = IgnoredActionsModifySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, action_type: Optional[ActionType]) -> Response:
        return self.rest_api.get_ignored_action_ids(action_type=action_type)

    @use_kwargs(modify_schema, location='json')
    def put(self, action_type: ActionType, action_ids: List[str]) -> Response:
        return self.rest_api.add_ignored_action_ids(action_type=action_type, action_ids=action_ids)

    @use_kwargs(modify_schema, location='json')
    def delete(self, action_type: ActionType, action_ids: List[str]) -> Response:
        return self.rest_api.remove_ignored_action_ids(
            action_type=action_type,
            action_ids=action_ids,
        )


class QueriedAddressesResource(BaseResource):

    modify_schema = QueriedAddressesSchema()

    def get(self) -> Response:
        return self.rest_api.get_queried_addresses_per_module()

    @use_kwargs(modify_schema, location='json')
    def put(self, module: ModuleName, address: ChecksumEthAddress) -> Response:
        return self.rest_api.add_queried_address_per_module(module=module, address=address)

    @use_kwargs(modify_schema, location='json')
    def delete(self, module: ModuleName, address: ChecksumEthAddress) -> Response:
        return self.rest_api.remove_queried_address_per_module(module=module, address=address)


class InfoResource(BaseResource):

    get_schema = AppInfoSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, check_for_updates: bool) -> Response:
        return self.rest_api.get_info(check_for_updates)


class PingResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.ping()


class DataImportResource(BaseResource):

    upload_schema = DataImportSchema()

    @use_kwargs(upload_schema, location='json')
    def put(
        self,
        source: IMPORTABLE_LOCATIONS,
        file: Path,
        timestamp_format: Optional[str],
    ) -> Response:
        return self.rest_api.import_data(
            source=source,
            filepath=file,
            timestamp_format=timestamp_format,
        )

    @use_kwargs(upload_schema, location='form_and_file')
    def post(
            self,
            source: IMPORTABLE_LOCATIONS,
            file: FileStorage,
            timestamp_format: Optional[str],
    ) -> Response:
        with TemporaryDirectory() as temp_directory:
            filename = file.filename if file.filename else f'{source}.csv'
            filepath = Path(temp_directory) / filename
            file.save(str(filepath))
            response = self.rest_api.import_data(
                source=source,
                filepath=filepath,
                timestamp_format=timestamp_format,
            )

        return response


class Eth2DailyStatsResource(BaseResource):
    post_schema = Eth2DailyStatsSchema()

    @use_kwargs(post_schema, location='json_and_query')
    def post(
            self,
            filter_query: Eth2DailyStatsFilterQuery,
            async_query: bool,
            only_cache: bool,
    ) -> Response:
        return self.rest_api.get_eth2_daily_stats(
            filter_query=filter_query,
            async_query=async_query,
            only_cache=only_cache,
        )


class Eth2ValidatorsResource(BaseResource):

    patch_schema = Eth2ValidatorPatchSchema()
    put_schema = Eth2ValidatorPutSchema()
    delete_schema = Eth2ValidatorDeleteSchema()

    def get(self) -> Response:
        return self.rest_api.get_eth2_validators()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            validator_index: Optional[int],
            public_key: Optional[Eth2PubKey],
            ownership_percentage: FVal,
            async_query: bool,
    ) -> Response:
        return self.rest_api.add_eth2_validator(
            validator_index=validator_index,
            public_key=public_key,
            ownership_proportion=ownership_percentage,
            async_query=async_query,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(self, validators: List[Dict[str, Any]]) -> Response:
        return self.rest_api.delete_eth2_validator(validators)

    @use_kwargs(patch_schema, location='json')
    def patch(self, validator_index: int, ownership_percentage: FVal) -> Response:
        return self.rest_api.edit_eth2_validator(
            validator_index=validator_index,
            ownership_proportion=ownership_percentage,
        )


class Eth2StakeDepositsResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_eth2_stake_deposits(async_query)


class Eth2StakeDetailsResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_eth2_stake_details(async_query)


class DefiBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_defi_balances(async_query)


class NamedEthereumModuleDataResource(BaseResource):
    delete_schema = NamedEthereumModuleDataSchema()

    @use_kwargs(delete_schema, location='view_args')
    def delete(self, module_name: ModuleName) -> Response:
        return self.rest_api.purge_module_data(module_name)


class EthereumModuleDataResource(BaseResource):

    def delete(self) -> Response:
        return self.rest_api.purge_module_data(module_name=None)


class EthereumModuleResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.supported_modules()


class MakerdaoDSRBalanceResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_makerdao_dsr_balance(async_query)


class MakerdaoDSRHistoryResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_makerdao_dsr_history(async_query)


class MakerdaoVaultsResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_makerdao_vaults(async_query)


class MakerdaoVaultDetailsResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_makerdao_vault_details(async_query)


class AaveBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_aave_balances(async_query)


class AaveHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_aave_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class AdexBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_adex_balances(async_query=async_query)


class AdexHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_adex_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class CompoundBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_compound_balances(async_query)


class CompoundHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_compound_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class YearnVaultsBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_yearn_vaults_balances(async_query)


class YearnVaultsV2BalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_yearn_vaults_v2_balances(async_query)


class YearnVaultsHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_yearn_vaults_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class YearnVaultsV2HistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_yearn_vaults_v2_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class UniswapBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_uniswap_balances(async_query=async_query)


class UniswapEventsHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_uniswap_events_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class UniswapTradesHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_uniswap_trades_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class SushiswapBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_sushiswap_balances(async_query=async_query)


class SushiswapEventsHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_sushiswap_events_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class SushiswapTradesHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_sushiswap_trades_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class LoopringBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_loopring_balances(async_query=async_query)


class LiquityTrovesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_liquity_troves(async_query=async_query)


class LiquityTrovesHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
        self,
        async_query: bool,
        reset_db_data: bool,
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_liquity_trove_events(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class LiquityStakingHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
        self,
        async_query: bool,
        reset_db_data: bool,
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_liquity_stake_events(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class LiquityStakingResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_liquity_staked(async_query=async_query)


class PickleDillResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_dill_balance(async_query=async_query)


class BalancerBalancesResource(BaseResource):

    get_schema = AsyncQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool) -> Response:
        return self.rest_api.get_balancer_balances(async_query=async_query)


class BalancerEventsHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_balancer_events_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class BalancerTradesHistoryResource(BaseResource):

    get_schema = AsyncHistoricalQuerySchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(
            self,
            async_query: bool,
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_balancer_trades_history(
            async_query=async_query,
            reset_db_data=reset_db_data,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class WatchersResource(BaseResource):

    put_schema = WatchersAddSchema
    patch_schema = WatchersEditSchema
    delete_schema = WatchersDeleteSchema

    def get(self) -> Response:
        return self.rest_api.get_watchers()

    @use_kwargs(put_schema, location='json')
    def put(self, watchers: List[Dict[str, Any]]) -> Response:
        return self.rest_api.add_watchers(watchers)

    @use_kwargs(patch_schema, location='json')
    def patch(self, watchers: List[Dict[str, Any]]) -> Response:
        return self.rest_api.edit_watchers(watchers)

    @use_kwargs(delete_schema, location='json')
    def delete(self, watchers: List[str]) -> Response:
        return self.rest_api.delete_watchers(watchers)


class AssetIconsResource(BaseResource):

    get_schema = SingleAssetIdentifierSchema()
    upload_schema = AssetIconUploadSchema()

    @use_kwargs(get_schema, location='view_args')
    def get(self, asset: Asset) -> Response:
        # Process the if-match and if-none-match headers so that comparison with etag can be done
        match_header = flask_request.headers.get('If-Match', None)
        if not match_header:
            match_header = flask_request.headers.get('If-None-Match', None)
        if match_header:
            match_header = match_header[1:-1]  # remove enclosing quotes

        return self.rest_api.get_asset_icon(asset, match_header)

    @use_kwargs(upload_schema, location='json_and_view_args')
    def put(self, asset: Asset, file: Path) -> Response:
        return self.rest_api.upload_asset_icon(asset=asset, filepath=file)

    @use_kwargs(upload_schema, location='view_args_and_file')
    def post(self, asset: Asset, file: FileStorage) -> Response:
        with TemporaryDirectory() as temp_directory:
            filename = file.filename if file.filename else f'{asset.identifier}.png'
            filepath = Path(temp_directory) / filename
            file.save(str(filepath))
            response = self.rest_api.upload_asset_icon(asset=asset, filepath=filepath)

        return response


class CurrentAssetsPriceResource(BaseResource):

    put_schema = ManualPriceSchema
    post_schema = CurrentAssetsPriceSchema()
    delete_schema = SingleAssetIdentifierSchema()

    def get(self) -> Response:
        return self.rest_api.get_nfts_with_price()

    @use_kwargs(put_schema, location='json')
    def put(
            self,
            from_asset: Asset,
            to_asset: Asset,
            price: Price,
    ) -> Response:
        return self.rest_api.add_manual_current_price(
            from_asset=from_asset,
            to_asset=to_asset,
            price=price,
        )

    @use_kwargs(post_schema, location='json')
    def post(
            self,
            assets: List[Asset],
            target_asset: Asset,
            ignore_cache: bool,
            async_query: bool,
    ) -> Response:
        return self.rest_api.get_current_assets_price(
            assets=assets,
            target_asset=target_asset,
            ignore_cache=ignore_cache,
            async_query=async_query,
        )

    @use_kwargs(delete_schema, location='json')
    def delete(self, asset: Asset) -> Response:
        return self.rest_api.delete_manual_current_price(asset)


class HistoricalAssetsPriceResource(BaseResource):

    post_schema = HistoricalAssetsPriceSchema()
    put_schema = TimedManualPriceSchema()
    patch_schema = TimedManualPriceSchema()
    get_schema = ManualPriceRegisteredSchema()
    delete_schema = ManualPriceDeleteSchema()

    @use_kwargs(post_schema, location='json')
    def post(
            self,
            assets_timestamp: List[Tuple[Asset, Timestamp]],
            target_asset: Asset,
            async_query: bool,
    ) -> Response:
        return self.rest_api.get_historical_assets_price(
            assets_timestamp=assets_timestamp,
            target_asset=target_asset,
            async_query=async_query,
        )

    @use_kwargs(put_schema, location='json')
    def put(
        self,
        from_asset: Asset,
        to_asset: Asset,
        price: Price,
        timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.add_manual_price(
            from_asset=from_asset,
            to_asset=to_asset,
            price=price,
            timestamp=timestamp,
        )

    @use_kwargs(patch_schema, location='json')
    def patch(
        self,
        from_asset: Asset,
        to_asset: Asset,
        price: Price,
        timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.edit_manual_price(
            from_asset=from_asset,
            to_asset=to_asset,
            price=price,
            timestamp=timestamp,
        )

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(self, from_asset: Optional[Asset], to_asset: Optional[Asset]) -> Response:
        return self.rest_api.get_manual_prices(from_asset, to_asset)

    @use_kwargs(delete_schema)
    def delete(
        self,
        from_asset: Asset,
        to_asset: Asset,
        timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.delete_manual_price(from_asset, to_asset, timestamp)


class NamedOracleCacheResource(BaseResource):

    post_schema = NamedOracleCacheCreateSchema()
    delete_schema = NamedOracleCacheSchema()
    get_schema = NamedOracleCacheGetSchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(self, oracle: HistoricalPriceOracle, async_query: bool) -> Response:
        return self.rest_api.get_oracle_cache(oracle=oracle, async_query=async_query)

    @use_kwargs(post_schema, location='json_and_view_args')
    def post(
            self,
            oracle: HistoricalPriceOracle,
            from_asset: Asset,
            to_asset: Asset,
            purge_old: bool,
            async_query: bool,
    ) -> Response:
        return self.rest_api.create_oracle_cache(
            oracle=oracle,
            from_asset=from_asset,
            to_asset=to_asset,
            purge_old=purge_old,
            async_query=async_query,
        )

    @use_kwargs(delete_schema, location='json_and_view_args')
    def delete(
            self,
            oracle: HistoricalPriceOracle,
            from_asset: Asset,
            to_asset: Asset,
    ) -> Response:
        return self.rest_api.delete_oracle_cache(
            oracle=oracle,
            from_asset=from_asset,
            to_asset=to_asset,
        )


class OraclesResource(BaseResource):

    def get(self) -> Response:
        return self.rest_api.get_supported_oracles()


class ERC20TokenInfo(BaseResource):

    get_schema = ERC20InfoSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, address: ChecksumEthAddress, async_query: bool) -> Response:
        return self.rest_api.get_token_information(address, async_query)


class BinanceAvailableMarkets(BaseResource):

    get_schema = BinanceMarketsSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, location: Location) -> Response:
        return self.rest_api.get_all_binance_pairs(location)


class BinanceUserMarkets(BaseResource):

    get_schema = BinanceMarketsUserSchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(self, name: str, location: Location) -> Response:
        return self.rest_api.get_user_binance_pairs(name, location)


class AvalancheTransactionsResource(BaseResource):
    get_schema = AvalancheTransactionQuerySchema()

    @use_kwargs(get_schema, location='json_and_query_and_view_args')
    def get(
        self,
        async_query: bool,
        address: Optional[ChecksumEthAddress],
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
    ) -> Response:
        return self.rest_api.get_avalanche_transactions(
            async_query=async_query,
            address=address,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )


class ERC20TokenInfoAVAX(BaseResource):
    get_schema = ERC20InfoSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, address: ChecksumEthAddress, async_query: bool) -> Response:
        return self.rest_api.get_avax_token_information(address, async_query)


class NFTSResource(BaseResource):
    get_schema = AsyncIgnoreCacheQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool, ignore_cache: bool) -> Response:
        return self.rest_api.get_nfts(async_query=async_query, ignore_cache=ignore_cache)


class NFTSBalanceResource(BaseResource):
    get_schema = AsyncIgnoreCacheQueryArgumentSchema()

    @use_kwargs(get_schema, location='json_and_query')
    def get(self, async_query: bool, ignore_cache: bool) -> Response:
        return self.rest_api.get_nfts_balances(async_query=async_query, ignore_cache=ignore_cache)


class StakingResource(BaseResource):
    get_schema = StakingQuerySchema

    @use_kwargs(get_schema, location='json_and_query')
    def post(
            self,
            async_query: bool,
            only_cache: bool,
            query_filter: 'HistoryEventFilterQuery',
            value_filter: 'HistoryEventFilterQuery',
    ) -> Response:
        return self.rest_api.query_kraken_staking_events(
            async_query=async_query,
            only_cache=only_cache,
            query_filter=query_filter,
            value_filter=value_filter,
        )


class UserAssetsResource(BaseResource):
    importing_schema = AssetsImportingSchema
    import_from_form = AssetsImportingFromFormSchema

    @use_kwargs(importing_schema, location='json')
    def put(self, file: Optional[Path], destination: Optional[Path], action: str) -> Response:
        if action == 'upload':
            return self.rest_api.import_user_assets(path=file)
        return self.rest_api.get_user_added_assets(path=destination)

    @use_kwargs(import_from_form, location='form_and_file')
    def post(self, file: FileStorage) -> Response:
        with TemporaryDirectory() as temp_directory:
            filename = file.filename if file.filename else 'assets.zip'
            filepath = Path(temp_directory) / filename
            file.save(str(filepath))
            response = self.rest_api.import_user_assets(path=filepath)
        return response


class DBSnapshotExportingResource(BaseResource):
    post_schema = SnapshotExportingSchema()

    @use_kwargs(post_schema, location='json')
    def post(self, timestamp: Timestamp, path: Path) -> Response:
        return self.rest_api.export_user_db_snapshot(timestamp=timestamp, path=path)


class DBSnapshotDownloadingResource(BaseResource):
    post_schema = SnapshotTimestampQuerySchema()

    @use_kwargs(post_schema, location='json')
    def post(self, timestamp: Timestamp) -> Response:
        return self.rest_api.download_user_db_snapshot(timestamp=timestamp)


class DBSnapshotImportingResource(BaseResource):
    post_schema = SnapshotImportingSchema()

    @use_kwargs(post_schema, location='json')
    def post(self, balances_snapshot_file: Path, location_data_snapshot_file: Path) -> Response:
        return self.rest_api.import_user_snapshot(
            balances_snapshot_file=balances_snapshot_file,
            location_data_snapshot_file=location_data_snapshot_file,
        )


class DBSnapshotDeletingResource(BaseResource):
    delete_schema = SnapshotTimestampQuerySchema()

    @use_kwargs(delete_schema, location='json')
    def delete(self, timestamp: Timestamp) -> Response:
        return self.rest_api.delete_user_db_snapshot(timestamp=timestamp)


class ReverseEnsResource(BaseResource):
    reverse_ens_schema = ReverseEnsSchema

    @use_kwargs(reverse_ens_schema, location='json_and_query')
    def post(self, ethereum_addresses: List[ChecksumEthAddress], force_update: bool) -> Response:
        return self.rest_api.get_ens_mappings(addresses=ethereum_addresses, force_update=force_update)  # noqa: E501
