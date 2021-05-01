import time
import sys
import copy

import ujson
import click

from .utils import (
    add_0lt,
    to_wei,
)


max_uint_value = int(0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)


class UniswapManager:

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
        self.fee_address = None
        self._state = {}
        self._state_path = state_path
        self._initial_data = data

    def load_state(self):
        try:
            with open(self._state_path, 'r', encoding='utf-8') as f:
                self._state = ujson.load(f)
                click.secho('State file loaded', fg='green')
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

    async def get_reserves(self, token0, token1):
        """Get reserves of the LP
        """
        factory_address = self._get_state("UniswapV2Factory")

        result = await self.client.call_method(factory_address, 'UniswapV2Factory', 'getPair', [
            self.client.prepare_address(token0),
            self.client.prepare_address(token1),
        ], self.get_initial_data())
        if not result.rstrip('0'):
            return [0, 0]

        pair_address = result[-40:]

        result = await self.client.call_method(pair_address, 'UniswapV2Pair', 'getReserves', [], self.get_initial_data())
        if not result.rstrip('0'):
            return [0, 0]
        
        return [int(result[:64], 16), int(result[64:128], 16)]

    async def check_balance(self, amount):
        """Checking deployer balance, raise error if not enough
        """
        click.echo("Checking balance before start...")
        deployer_address = self.client.get_deployer_address()
        deployer_balance = await self.client.get_balance(deployer_address)
        
        if deployer_balance <= amount:
            click.secho(f"Balance if not enough to deploy (current: {deployer_balance}, required: {amount})", fg='red')
            sys.exit(1)
        click.echo(f'Balance loaded - {deployer_balance}')

    async def test_deploy(self, token_rate: str, count: int, dai_mint: int):
        """Test deploy for check if Uniswap works
        """
        assert self.fee_address is not None, "Fee address must be set"
        assert self.client is not None, "Client not initialized"

        initial_liquidity_olt = int(count)
        initial_liquidity_dai = int(initial_liquidity_olt * float(token_rate))

        click.echo(f'Initial liquidity for OLT: {initial_liquidity_olt}')
        click.echo(f'Initial liquidity for DAI: {initial_liquidity_dai}')
        click.echo(f'Initial DAI tokens to mint: {dai_mint}')

        await self.check_balance(initial_liquidity_olt)

        wolt_address = await self.smart_deploy("WOLT")
        factory_address = await self.smart_deploy("UniswapV2Factory", [self.fee_address])

        await self.smart_deploy("UniswapV2Router", [
            self.client.prepare_address(factory_address),
            self.client.prepare_address(wolt_address)
        ])
        # NOTE: Not a mandatory as it will be automatically converted during addLiquidityETH
        # await self.swap_olt_to_wolt(initial_liquidity_olt)
        await self.erc20_approve("WOLT")

        await self.deploy_and_mint_DAI(dai_mint)
        await self.erc20_approve("DAI")

        await self.add_liquidity_OLT("DAI", initial_liquidity_olt, initial_liquidity_dai, force=False)

        click.secho('Done! Initial setup set successfully.', fg='green')

    async def swap_olt_to_wolt(self, amount):
        """Swap between OLT to WOLT tokens
        """
        deployer_address = self.client.get_deployer_address()
        erc20_contract = self._get_state('WOLT')

        click.echo(f"Checking address '{add_0lt(deployer_address)}' on initial balance of WOLT...")
        result = await self.client.call_method(erc20_contract, 'WOLT', 'balanceOf', [
            self.client.prepare_address(deployer_address),
        ], self.get_initial_data())

        balance_of = int(result, 16)
        if balance_of < amount:
            if not balance_of:
                delta_wei = amount
                click.echo(f"Balance not found, swaping OLT to WOLT...")
            else:
                delta_wei = amount - balance_of
                click.echo(f"Not enough balance for the next steps (required: {amount}, on balance: {balance_of}), adding delta {delta_wei} for swap")
                amount = delta_wei

            data = self.get_initial_data()
            data["amount"]["value"] = str(amount)
            done, _ = await self.client.execute_method(erc20_contract, 'WOLT', 'deposit', [], data)
            assert done is True, "WOLT not exchanged"
            result = await self.client.call_method(erc20_contract, 'WOLT', 'balanceOf', [
                self.client.prepare_address(deployer_address),
            ], self.get_initial_data())
            balance_of = int(result, 16)
            assert balance_of >= amount, "Initial balance not set, revert"
            click.secho(f"{delta_wei} tokens was moved from OLT to WOLT for an address '{add_0lt(deployer_address)}'", fg='green')
        else:
            click.echo(f"Balance of '{add_0lt(deployer_address)}' filled, skipping...")

    async def deploy_and_mint_DAI(self, amount):
        """Deploy and mint DAI
        """
        token_name = 'DAI'
        deployer_address = self.client.get_deployer_address()
        data = self.get_initial_data()

        erc20_address = await self.smart_deploy(token_name)

        click.echo(f"Checking address '{add_0lt(deployer_address)}' on initial balance of {token_name}...")
        result = await self.client.call_method(erc20_address, token_name, 'totalSupply', [], data)

        balance_of = int(result, 16)
        if not balance_of:
            click.echo(f"Balance not found, minting DAI...")

            done, _ = await self.client.execute_method(erc20_address, 'DAI', 'mint', [
                self.client.prepare_address(deployer_address),
                amount,
            ], data)
            if not done:
                click.secho('DAI not minted.', fg='red')
                sys.exit(1)

            result = await self.client.call_method(erc20_address, 'DAI', 'totalSupply', [], data)
            balance_of = int(result, 16)
            if balance_of < amount:
                click.secho('Initial supply not set, revert.', fg='red')
                sys.exit(1)

            click.secho(f"{amount} tokens was minted and add for an address '{add_0lt(deployer_address)}'", fg='green')
        else:
            click.echo(f"Balance of '{add_0lt(deployer_address)}' filled, skipping...")

    async def erc20_approve(self, abi_name, value=max_uint_value):
        """Check ERC20 approvement for Uniswap Router
        """
        erc20_address = self._get_state(abi_name)
        router_address = self._get_state('UniswapV2Router')
        deployer_address = self.client.get_deployer_address()

        click.echo(f"Check router on allowance '{add_0lt(router_address)}' for {abi_name}...")
        result = await self.client.call_method(erc20_address, abi_name, 'allowance', [
            self.client.prepare_address(deployer_address),
            self.client.prepare_address(router_address),
        ], self.get_initial_data())
        allowance = int(result, 16)
        if allowance != value:
            click.echo(f"Approving router address '{add_0lt(router_address)}' for {abi_name}...")
            done, _ = await self.client.execute_method(erc20_address, abi_name, 'approve', [
                self.client.prepare_address(router_address),
                value,
            ], self.get_initial_data())
            if not done:
                click.secho(f'{abi_name} not approved.', fg='red')
                sys.exit(1)

            click.secho(f"{abi_name} approved for router '{router_address}'", fg='green')
        else:
            click.secho(f"Allready allowance set for address '{add_0lt(router_address)}'", fg='green')

    async def add_liquidity_OLT(self, abi_name1_or_token1, amount0, amount1, min_amount0=0, min_amount1=0, deadline=300, force=True):
        """Add liquidity with wrapped token
        """
        factory_address = self._get_state('UniswapV2Factory')
        router_address = self._get_state('UniswapV2Router')
        token0 = self.get_WOLT_address()
        token1 = abi_name1_or_token1 if len(abi_name1_or_token1) == 40 else self._get_state(abi_name1_or_token1)

        deployer_address = self.client.get_deployer_address()

        reserves = await self.get_reserves(token0, token1)
        if force or reserves[0] == 0 and reserves[1] == 0:
            data = self.get_initial_data()
            data["amount"]["value"] = str(amount0)

            click.echo(f'Adding the liquidity on router "{add_0lt(router_address)}" for ["{add_0lt(token0)}", "{add_0lt(token1)}"] with amounts - [{amount0}, {amount1}] ')
            done, tx_hash = await self.client.execute_method(router_address, 'UniswapV2Router', 'addLiquidityETH', [
                self.client.prepare_address(token1),
                amount1,
                min_amount1,
                min_amount0,
                self.client.prepare_address(deployer_address),
                int(time.time() + deadline),
            ], data)
            assert done, f"Liquidity pair failed, please check '{tx_hash}' for more details"

            result = await self.client.call_method(factory_address, 'UniswapV2Factory', 'getPair', [
                self.client.prepare_address(token0),
                self.client.prepare_address(token1),
            ], self.get_initial_data())
            assert result.rstrip('0'), "Failed to get address of the pair"

            pair_address = result[-40:]
            self.update_state(f"UniswapV2Pair_{token0}_{token0}", {
                "address": pair_address,
                "tx_hash": tx_hash,
            })

            reserves = await self.get_reserves(token0, token1)
            assert reserves[0] != 0, "reserve0 is zero"
            assert reserves[1] != 0, "reserve1 is zero"
            click.secho(f'Liquidity filled!', fg='green')
        else:
            click.echo(f'Liquidity already filled for pair ["0lt{token0}", "0lt{token1}"] - reserve0: {reserves[0]}, reserve1: {reserves[1]}')
