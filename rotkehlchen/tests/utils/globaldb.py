from contextlib import ExitStack
from copy import deepcopy
from typing import Literal
from unittest.mock import patch

from rotkehlchen.assets.asset import EvmToken, UnderlyingToken
from rotkehlchen.constants.assets import A_MKR
from rotkehlchen.fval import FVal
from rotkehlchen.globaldb.upgrades.manager import UPGRADES_LIST
from rotkehlchen.tests.utils.factories import make_evm_address
from rotkehlchen.types import ChainID, EvmTokenKind, Timestamp

underlying_address1 = make_evm_address()
underlying_address2 = make_evm_address()
underlying_address3 = make_evm_address()

user_token_address1 = make_evm_address()
user_token_address2 = make_evm_address()


def create_initial_globaldb_test_tokens() -> list[EvmToken]:
    return [
        EvmToken.initialize(
            address=user_token_address1,
            chain_id=ChainID.ETHEREUM,
            token_kind=EvmTokenKind.ERC20,
            decimals=4,
            name='Custom 1',
            symbol='CST1',
            started=Timestamp(0),
            swapped_for=A_MKR.resolve_to_crypto_asset(),
            coingecko='internet-computer',
            cryptocompare='ICP',
            protocol='uniswap',
            underlying_tokens=[
                UnderlyingToken(address=underlying_address1, token_kind=EvmTokenKind.ERC20, weight=FVal('0.5055')),  # noqa: E501
                UnderlyingToken(address=underlying_address2, token_kind=EvmTokenKind.ERC20, weight=FVal('0.1545')),  # noqa: E501
                UnderlyingToken(address=underlying_address3, token_kind=EvmTokenKind.ERC20, weight=FVal('0.34')),  # noqa: E501
            ],
        ),
        EvmToken.initialize(
            address=user_token_address2,
            chain_id=ChainID.ETHEREUM,
            token_kind=EvmTokenKind.ERC20,
            decimals=18,
            name='Custom 2',
            symbol='CST2',
        ),
    ]


def create_initial_expected_globaldb_test_tokens():
    initial_tokens = create_initial_globaldb_test_tokens()
    return [initial_tokens[0]] + [
        EvmToken.initialize(underlying_address1, chain_id=ChainID.ETHEREUM, token_kind=EvmTokenKind.ERC20),  # noqa: E501
        EvmToken.initialize(underlying_address2, chain_id=ChainID.ETHEREUM, token_kind=EvmTokenKind.ERC20),  # noqa: E501
        EvmToken.initialize(underlying_address3, chain_id=ChainID.ETHEREUM, token_kind=EvmTokenKind.ERC20),  # noqa: E501
    ] + [initial_tokens[1]]


underlying_address4 = make_evm_address()
user_token_address3 = make_evm_address()
USER_TOKEN3 = EvmToken.initialize(
    address=user_token_address3,
    chain_id=ChainID.ETHEREUM,
    token_kind=EvmTokenKind.ERC20,
    decimals=15,
    name='Custom 3',
    symbol='CST3',
    cryptocompare='ICP',
    protocol='aave',
    underlying_tokens=[
        UnderlyingToken(address=user_token_address1, token_kind=EvmTokenKind.ERC20, weight=FVal('0.55')),  # noqa: E501
        UnderlyingToken(address=underlying_address4, token_kind=EvmTokenKind.ERC20, weight=FVal('0.45')),  # noqa: E501
    ],
)


def patch_for_globaldb_upgrade_to(stack: ExitStack, version: Literal[2, 3]) -> ExitStack:
    stack.enter_context(
        patch(
            'rotkehlchen.globaldb.upgrades.manager.GLOBAL_DB_VERSION',
            version,
        ),
    )
    original_list = deepcopy(UPGRADES_LIST)
    stack.enter_context(
        patch(
            'rotkehlchen.globaldb.upgrades.manager.UPGRADES_LIST',
            original_list[:version - 2],
        ),
    )
    return stack
