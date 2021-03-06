import time
import sys
import copy
from decimal import Decimal

import ujson
import click
from web3 import Web3

from .utils import (
    add_0lt,
    remove_0x,
    to_wei,
    pretty_float,
)


class UniswapUtils:

    @staticmethod
    def _get_change(current, previous):
        if current == previous:
            return 0
        try:
            return round((abs(current - previous) / previous) * 100, 2)
        except ZeroDivisionError:
            return float('inf')

    @classmethod
    def calculate_min_slippage_amount(cls, amount, slippage):
        """Calculate min slippage amount
        """
        slippage = round(slippage, 2)
        spercent = slippage / 100
        slippaged = Web3.toWei(Web3.fromWei(amount, 'wei') * Decimal(1 - spercent), 'wei')
        if cls._get_change(slippaged, amount) > slippage:
            # colud not perform when tokens not enough
            return 0
        return slippaged

    @classmethod
    def calculate_max_slippage_amount(cls, amount, slippage):
        """Calculate max slippage amount
        """
        slippage = round(slippage, 2)
        spercent = slippage / 100
        return Web3.toWei(Web3.fromWei(amount, 'wei') * Decimal(1 + spercent), 'wei')

    @staticmethod
    def sort_tokens(tokenA, tokenB):
        """Get sorted tokens
        """
        assert tokenA != tokenB, "Identical addresses"
        return [tokenA, tokenB] if tokenA < tokenB else [tokenB, tokenA]


class UniswapManager:

    MAX_UINT_VALUE = int(0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

    DEFAULT_DATA = {
        "amount": {
            "currency": "OLT",
            "value": "0",
        },
        "gas": 10_000_000,
        "gasPrice": {
            "currency": "OLT",
            "value": str(1 * 10 ** 9), # something like gwei
        },
    }

    def __init__(self, state_path, data=DEFAULT_DATA.copy()):
        self.client = None
        self.sl = None
        self.fee_address = None
        self._state = {}
        self._state_path = state_path
        self._initial_data = data

    def load_state(self):
        try:
            with open(self._state_path, 'r', encoding='utf-8') as f:
                self._state = ujson.load(f)
                click.secho('State file loaded', fg='blue')
        except FileNotFoundError:
            click.secho('State file not found, skipping', fg='red')
        except ValueError:
            click.secho('State file broken, skipping', fg='red')

    def update_state(self, key, value):
        self._state[key] = value
        with open(self._state_path, 'w', encoding='utf-8') as f:
            ujson.dump(self._state, f, ensure_ascii=False, indent=8)

    def use_client(self, client):
        self.client = client

    def use_swap_list(self, sl):
        self.sl = sl

    def set_fee(self, address):
        assert self.client is not None, "Client must be initialized first"
        self.fee_address = self.client.prepare_address(address)

    def get_initial_data(self):
        # Copy nested to make it reusable
        return copy.deepcopy(self._initial_data)

    def get_WOLT_address(self):
        return self._get_state('WOLT')

    def _get_state(self, abi_name, key='address'):
        try:
            contract_data = self._state[abi_name]
        except KeyError:
            click.secho(f'{abi_name} contract not found, revert.', fg='red')
            sys.exit(1)

        return contract_data[key]

    async def get_token_name(self, token):
        return await self.client.call_method(token, 'WOLT', 'symbol', [], self.get_initial_data())

    async def get_token_decimals(self, token):
        return await self.client.call_method(token, 'WOLT', 'decimals', [], self.get_initial_data())

    async def get_total_supply(self, token):
        return await self.client.call_method(token, 'WOLT', 'totalSupply', [], self.get_initial_data())

    async def smart_deploy(self, abi_name, params=None, amount=0):
        """Smart deploy used to create or take a contract for cache
        """
        if params is None:
            params = []

        if abi_name in self._state:
            click.secho(f"{abi_name}: get address from previous deployment")
            address = self._state[abi_name]["address"]
        else:
            click.secho(f"{abi_name}: deploying...")

            data = self.get_initial_data()
            data["amount"]["value"] = str(amount)
            address, tx_hash = await self.client.execute_smart_contract(abi_name, 'constructor', params, data)
            self.update_state(abi_name, {
                "address": address,
                "tx_hash": tx_hash,
            })
            click.secho(f"{abi_name}: done, address: '{address}'")
        return address

    async def get_pair(self, token0, token1):
        factory_address = self._get_state("UniswapV2Factory")

        result = await self.client.call_method(factory_address, 'UniswapV2Factory', 'getPair', [
            self.client.prepare_address(token0),
            self.client.prepare_address(token1),
        ], self.get_initial_data())
        pair_address = remove_0x(result)
        if pair_address == '0' * 40:
            return
        return pair_address

    async def get_reserves(self, token0, token1):
        """Get reserves of the LP
        """
        factory_address = self._get_state("UniswapV2Factory")

        pair_address = await self.get_pair(token0, token1)
        if not pair_address:
            return [0, 0]

        result = await self.client.call_method(pair_address, 'UniswapV2Pair', 'getReserves', [], self.get_initial_data())
        return [result[0], result[1]]

    async def check_balance(self, amount):
        """Checking deployer balance, raise error if not enough
        """
        click.echo("Checking balance before start...")
        deployer_address = self.client.get_deployer_address()
        deployer_balance = await self.client.get_balance(deployer_address)
        
        if deployer_balance <= amount:
            click.secho(f"Balance if not enough to deploy (current: {pretty_float(deployer_balance, 18)}, required: {pretty_float(amount, 18)})", fg='red')
            sys.exit(1)
        click.echo(f'Balance loaded - {pretty_float(deployer_balance, 18)}')

    async def deploy_and_mint_DAI(self, amount):
        """Deploy and mint DAI
        """
        token_name = 'DAI'
        deployer_address = self.client.get_deployer_address()
        data = self.get_initial_data()

        erc20_address = await self.smart_deploy(token_name)

        click.echo(f"Checking address '{add_0lt(deployer_address)}' on initial balance of {token_name}...")
        totalSupply = await self.get_total_supply(erc20_address)
        if not totalSupply:
            click.echo(f"Balance not found, minting DAI...")

            done, _ = await self.client.execute_method(erc20_address, 'DAI', 'mint', [
                self.client.prepare_address(deployer_address),
                amount,
            ], data)
            if not done:
                click.secho('DAI not minted.', fg='red')
                sys.exit(1)

            totalSupply = await self.get_total_supply(erc20_address)
            if totalSupply < amount:
                click.secho('Initial supply not set, revert.', fg='red')
                sys.exit(1)

            click.secho(f"{pretty_float(amount, 18)} tokens was minted and add for an address '{add_0lt(deployer_address)}'", fg='green')
        else:
            click.echo(f"Balance of '{add_0lt(deployer_address)}' filled, skipping...")
        return erc20_address

    async def get_token_balance(self, token, address):
        return await self.client.call_method(token, 'WOLT', 'balanceOf', [self.client.prepare_address(address)], self.get_initial_data())

    async def get_pair(self, token0, token1):
        factory_address = self._get_state('UniswapV2Factory')

        result = await self.client.call_method(factory_address, 'UniswapV2Factory', 'getPair', [
            self.client.prepare_address(token0),
            self.client.prepare_address(token1),
        ], self.get_initial_data())
        pair_address = remove_0x(result)
        if pair_address == '0' * 40:
            return
        return pair_address

    async def erc20_approve(self, erc20_address, address, value=MAX_UINT_VALUE):
        """Check ERC20 approvement for an address
        """
        name = await self.get_token_name(erc20_address)
        deployer_address = self.client.get_deployer_address()

        click.echo(f"Check address on allowance '{add_0lt(address)}' for {name}...")
        allowance = await self.client.call_method(erc20_address, 'WOLT', 'allowance', [
            self.client.prepare_address(deployer_address),
            self.client.prepare_address(address),
        ], self.get_initial_data())
        if allowance != value:
            click.echo(f"Approving address address '{add_0lt(address)}' for {name}...")
            done, _ = await self.client.execute_method(erc20_address, 'WOLT', 'approve', [
                self.client.prepare_address(address),
                value,
            ], self.get_initial_data())
            assert done, f'{name} not approved.'
            click.secho(f"{name} approved for address '{add_0lt(address)}'", fg='green')
        else:
            click.secho(f"Allready allowance set for address '{add_0lt(address)}'", fg='green')

    async def add_liquidity_OLT(self, token1, amount0, amount1, min_amount0=0, min_amount1=0, deadline=300, force=True):
        """Add liquidity with wrapped token
        """
        factory_address = self._get_state('UniswapV2Factory')
        router_address = self._get_state('UniswapV2Router')
        token0 = self.get_WOLT_address()
        deployer_address = self.client.get_deployer_address()

        reserves = await self.get_reserves(token0, token1)
        if force or reserves[0] == 0 and reserves[1] == 0:
            data = self.get_initial_data()
            data["amount"]["value"] = str(amount0)

            click.echo(f'Adding the liquidity OLT on router "{add_0lt(router_address)}" for ["{add_0lt(token0)}", "{add_0lt(token1)}"] with amounts - [{amount0}, {amount1}] ')
            done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'addLiquidityETH', [
                self.client.prepare_address(token1),
                amount1,
                min_amount1,
                min_amount0,
                self.client.prepare_address(deployer_address),
                int(time.time() + deadline),
            ], data)
            assert done, f"Liquidity pair failed, please check '{tx_hash}' for more details"

            reserves = await self.get_reserves(token0, token1)
            assert reserves[0] != 0, "reserve0 is zero"
            assert reserves[1] != 0, "reserve1 is zero"
            click.secho(f'Liquidity OLT filled!', fg='green')
        else:
            click.echo(f'Liquidity OLT already filled for pair ["{add_0lt(token0)}", "{add_0lt(token1)}"] - reserve0: {reserves[0]}, reserve1: {reserves[1]}')

    async def add_liquidity(self, token0, token1, amount0, amount1, min_amount0=0, min_amount1=0, deadline=300, force=True):
        """Add liquidity with LP tokens
        """
        factory_address = self._get_state('UniswapV2Factory')
        router_address = self._get_state('UniswapV2Router')
        deployer_address = self.client.get_deployer_address()

        reserves = await self.get_reserves(token0, token1)
        if force or reserves[0] == 0 and reserves[1] == 0:
            click.echo(f'Adding the liquidity on router "{add_0lt(router_address)}" for ["{add_0lt(token0)}", "{add_0lt(token1)}"] with amounts - [{amount0}, {amount1}] ')
            # checking before execution for uniswap error
            await self.client.call_method(router_address, 'UniswapV2Router', 'addLiquidity', [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
                amount0,
                amount1,
                min_amount0,
                min_amount1,
                self.client.prepare_address(deployer_address),
                int(time.time() + deadline),
            ], self.get_initial_data())

            done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'addLiquidity', [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
                amount0,
                amount1,
                min_amount0,
                min_amount1,
                self.client.prepare_address(deployer_address),
                int(time.time() + deadline),
            ], self.get_initial_data())
            assert done, f"Liquidity pair failed, please check '{tx_hash}' for more details"

            reserves = await self.get_reserves(token0, token1)
            assert reserves[0] != 0, "reserve0 is zero"
            assert reserves[1] != 0, "reserve1 is zero"
            click.secho(f'Liquidity filled!', fg='green')
        else:
            click.echo(f'Liquidity already filled for pair ["{add_0lt(token0)}", "{add_0lt(token1)}"] - reserve0: {reserves[0]}, reserve1: {reserves[1]}')

    async def remove_liquidity_OLT(self, token1, liquidity, min_amount0=0, min_amount1=0, deadline=300):
        """Remove liquidity with wrapped token
        """
        factory_address = self._get_state('UniswapV2Factory')
        router_address = self._get_state('UniswapV2Router')
        token0 = self.get_WOLT_address()
        deployer_address = self.client.get_deployer_address()

        click.echo(f'Removing the liquidity OLT on router "{add_0lt(router_address)}" for ["{add_0lt(token0)}", "{add_0lt(token1)}"] with liquidity - [{liquidity}] ')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'removeLiquidityETH', [
            self.client.prepare_address(token1),
            liquidity,
            min_amount1,
            min_amount0,
            self.client.prepare_address(deployer_address),
            int(time.time() + deadline),
        ], self.get_initial_data())

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'removeLiquidityETH', [
            self.client.prepare_address(token1),
            liquidity,
            min_amount1,
            min_amount0,
            self.client.prepare_address(deployer_address),
            int(time.time() + deadline),
        ], self.get_initial_data())
        assert done, f"Liquidity pair failed, please check '{tx_hash}' for more details"
        click.secho(f'Liquidity OLT burned!', fg='green')

    async def remove_liquidity(self, token0, token1, liquidity, min_amount0=0, min_amount1=0, deadline=300):
        """Remove liquidity with wrapped token
        """
        factory_address = self._get_state('UniswapV2Factory')
        router_address = self._get_state('UniswapV2Router')
        deployer_address = self.client.get_deployer_address()

        click.echo(f'Removing the liquidity on router "{add_0lt(router_address)}" for ["{add_0lt(token0)}", "{add_0lt(token1)}"] with liquidity - [{liquidity}] ')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'removeLiquidity', [
            self.client.prepare_address(token0),
            self.client.prepare_address(token1),
            liquidity,
            min_amount0,
            min_amount1,
            self.client.prepare_address(deployer_address),
            int(time.time() + deadline),
        ], self.get_initial_data())

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'removeLiquidity', [
            self.client.prepare_address(token0),
            self.client.prepare_address(token1),
            liquidity,
            min_amount0,
            min_amount1,
            self.client.prepare_address(deployer_address),
            int(time.time() + deadline),
        ], self.get_initial_data())
        assert done, f"Liquidity pair failed, please check '{tx_hash}' for more details"
        click.secho(f'Liquidity burned!', fg='green')

    async def quote(self, amount0, reserve0, reserve1):
        """Used to determine the quote
        """
        router_address = self._get_state('UniswapV2Router')

        return await self.client.call_method(router_address, 'UniswapV2Router', 'quote', [
            amount0,
            reserve0,
            reserve1
        ], self.get_initial_data())

    async def get_amounts_out(self, amount, token0, token1):
        """Used to determine the amounts for out
        """
        router_address = self._get_state('UniswapV2Router')

        return await self.client.call_method(router_address, 'UniswapV2Router', 'getAmountsOut', [
            amount,
            [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
            ]
        ], self.get_initial_data())

    async def get_amounts_in(self, amount, token0, token1):
        """Used to determine the amounts for in
        """
        router_address = self._get_state('UniswapV2Router')

        return await self.client.call_method(router_address, 'UniswapV2Router', 'getAmountsIn', [
            amount,
            [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
            ]
        ], self.get_initial_data())

    async def get_pool_fee_rate(self):
        """Get uniswap pool fee rate
        """
        router_address = self._get_state('UniswapV2Router')

        info = await self.client.call_method(router_address, 'UniswapV2Router', 'getPoolFeeRate', [], self.get_initial_data())
        return 1 - Decimal(info[0]) / 10 ** info[1]

    async def swap_tokens_for_exact_OLT(self, token, amount_in_max, amount_out, to, deadline):
        """Perform swap from OLT -> ERC20 token for receive with amount with specified max on swap
        """
        wolt_token = self.sl.get('WOLT')
        router_address = self._get_state('UniswapV2Router')
        data = self.get_initial_data()

        click.echo(f'Starting to perform swap tokens for exact OLT on router "{add_0lt(router_address)}"...')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'swapTokensForExactETH', [
            amount_out,
            amount_in_max,
            [
                self.client.prepare_address(token),
                self.client.prepare_address(wolt_token),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], data)

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'swapTokensForExactETH', [
            amount_out,
            amount_in_max,
            [
                self.client.prepare_address(token),
                self.client.prepare_address(wolt_token),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], data)
        assert done, f"Swap failed, please check '{tx_hash}' for more details"
        click.secho(f'Swap done!', fg='green')

    async def swap_exact_OLT_for_tokens(self, token, amount_in, amount_out_min, to, deadline):
        """Perform swap from OLT -> ERC20 token for receive with min amount
        """
        wolt_token = self.sl.get('WOLT')
        router_address = self._get_state('UniswapV2Router')
        data = self.get_initial_data()
        data["amount"]["value"] = str(amount_in)

        click.echo(f'Starting to perform swap exact OLT for tokens on router "{add_0lt(router_address)}"...')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'swapExactETHForTokens', [
            amount_out_min,
            [
                self.client.prepare_address(wolt_token),
                self.client.prepare_address(token),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], data)

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'swapExactETHForTokens', [
            amount_out_min,
            [
                self.client.prepare_address(wolt_token),
                self.client.prepare_address(token),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], data)
        assert done, f"Swap failed, please check '{tx_hash}' for more details"
        click.secho(f'Swap done!', fg='green')

    async def swap_tokens_for_exact_tokens(self, token0, token1, amount_in_max, amount_out, to, deadline):
        """Perform swap from ERC20 -> ERC20 token for receive with amount with specified max on swap
        """
        router_address = self._get_state('UniswapV2Router')

        click.echo(f'Starting to perform swap tokens for exact tokens on router "{add_0lt(router_address)}"...')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'swapTokensForExactTokens', [
            amount_out,
            amount_in_max,
            [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], self.get_initial_data())

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'swapTokensForExactTokens', [
            amount_out,
            amount_in_max,
            [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], self.get_initial_data())
        assert done, f"Swap failed, please check '{tx_hash}' for more details"
        click.secho(f'Swap done!', fg='green')

    async def swap_exact_tokens_for_tokens(self, token0, token1, amount_in, amount_out_min, to, deadline):
        """Perform swap from ERC20 -> ERC20 token for receive with amount with min amount
        """
        router_address = self._get_state('UniswapV2Router')

        click.echo(f'Starting to perform swap exact tokens for tokens on router "{add_0lt(router_address)}"...')
        # checking before execution for uniswap error
        await self.client.call_method(router_address, 'UniswapV2Router', 'swapExactTokensForTokens', [
            amount_in,
            amount_out_min,
            [
                self.client.prepare_address(token1),
                self.client.prepare_address(token0),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], self.get_initial_data())

        done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'swapExactTokensForTokens', [
            amount_in,
            amount_out_min,
            [
                self.client.prepare_address(token1),
                self.client.prepare_address(token0),
            ],
            self.client.prepare_address(to),
            int(time.time() + deadline),
        ], self.get_initial_data())
        assert done, f"Swap failed, please check '{tx_hash}' for more details"
        click.secho(f'Swap done!', fg='green')
