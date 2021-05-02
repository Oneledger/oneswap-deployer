import asyncio
import binascii

import ujson
import aiohttp
import click
from web3 import Web3

from .utils import (
    parse_events,
    prepare_payload,
    get_signed_params,
    get_contract_data,
    bytecode_to_bytes,
    add_0lt,
    get_address_from_private_key,
    parse_call_response,
)


class WaitError(Exception):
    """When tx not found or error
    """
    pass


class ProtocolAPIError(Exception):
    """When protocol RPC error
    """
    pass


class Client:

    def __init__(self, url, private_key):
        self.url = url
        self.id = 0
        self._web3 = Web3()
        self._private_key = private_key

    async def _rpc_call(self, method, params, timeout=10):
        self.id += 1
        payload = {
            "method": method,
            "params": params,
            "id": self.id,
            "jsonrpc": "2.0"
        }
        async with aiohttp.ClientSession(
            json_serialize=ujson.dumps,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(self.url, json=payload, headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            }) as response:
                return await response.json()

    def get_deployer_address(self):
        return get_address_from_private_key(self._private_key, None)

    def prepare_address(self, address):
        return Web3.toChecksumAddress(address)

    async def get_account(self, address):
        """Get account for an address
        """
        resp = await self._rpc_call('query.EVMAccount', {
            "address": address,
            "blockTag": "latest",
        })
        return resp["result"]

    async def get_nonce(self, address):
        """Wrapper to get a nonce
        """
        account = await self.get_account(address)
        return account['nonce']

    async def get_balance(self, address):
        """Wrapper to get a balance
        """
        account = await self.get_account(address)
        balance = account.get('balance', '0') or '0'
        return int(balance)

    async def wait_for_tx(self, tx_hash, repeat_count=25):
        """Used to wait a specific transaction per hash
        """
        error = None
        while repeat_count:
            resp = await self._rpc_call('query.Tx', {
                "hash": tx_hash,
                "prove": True,
            })
            error = resp.get('error')
            if not error:
                break

            click.echo(f"Transaction '{tx_hash}' not mined, waiting...")

            await asyncio.sleep(1)
            repeat_count -= 1

        if error:
            raise WaitError(f"Failed to broadcast tx ({tx_hash}), details: {error['message']}")

        tx_result = resp['result']['result']['tx_result']
        click.echo(f"TX: gas used: {tx_result['gasUsed']}")
        click.echo(f"TX: gas wanted: {tx_result['gasWanted']}")
        if tx_result['code'] == 1:
            raise WaitError(f"Failed to execute tx ({tx_hash}), details: {tx_result['log']}")

        events = parse_events(tx_result['events'])
        if events.get('tx.error'):
            raise WaitError(f"Failed to execute vm ({tx_hash}), details: {events['tx.error']}")

        
        click.secho(f"Transaction '{tx_hash}' has been mined!\n", fg='green')
        return events

    async def create_raw_tx(self, params):
        """Creating raw tx data
        """
        resp = await self._rpc_call('tx.CreateRawSend', params)
        if resp.get('error'):
            raise ProtocolAPIError(f"Failed to create raw tx, details: {resp['error']['message']}")
        return resp["result"]["rawTx"]

    async def broadcast_tx(self, raw_tx):
        params = get_signed_params(raw_tx, self._private_key)
        resp = await self._rpc_call('broadcast.TxSync', params)
        if resp.get('error'):
            raise ProtocolAPIError(f"Failed to broadcast tx, details: {resp['error']['message']}")
        return resp['result']['txHash']

    async def execute_smart_contract(self, abi_name, fn_name, params, data):
        abi, bytecode = get_contract_data(abi_name)

        SmartContract = self._web3.eth.contract(abi=abi, bytecode=bytecode)
        if fn_name == 'constructor':
            bytecode = SmartContract.constructor(*params).data_in_transaction
        else:
            fn = getattr(SmartContract.functions, fn_name)(*params)
            bytecode = fn._encode_transaction_data()

        data["data"] = bytecode_to_bytes(bytecode)
        params = prepare_payload(data, self._private_key)
        if "nonce" not in params:
            params["nonce"] = await self.get_nonce(params["from"])

        click.echo("Creating raw tx...")
        raw_tx = await self.create_raw_tx(params)
        click.echo(f"Raw tx created.")

        click.echo(f"Broadcasting to the network...")
        transaction_hash = await self.broadcast_tx(raw_tx)
        click.echo(f"Transaction broadcasted, hash: {transaction_hash}")

        result = await self.wait_for_tx(transaction_hash)
        contract_address = result.get('tx.contract')
        if contract_address:
            return contract_address, transaction_hash

        return bool(binascii.hexlify(result['tx.status'].encode()).decode().rstrip('0')), transaction_hash

    async def execute_method(self, contract_address, abi_name, fn_name, params, data):
        abi, _ = get_contract_data(abi_name)

        SmartContract = self._web3.eth.contract(abi=abi, address=self.prepare_address(contract_address))
        function = getattr(SmartContract.functions, fn_name)
        fn = function(*params)
        bytecode = fn._encode_transaction_data()

        data["to"] = add_0lt(contract_address)
        data["data"] = bytecode_to_bytes(bytecode)
        return await self.execute_smart_contract(abi_name, fn_name, params, data)

    async def call_method(self, contract_address, abi_name, fn_name, params, data):
        abi, _ = get_contract_data(abi_name)

        SmartContract = self._web3.eth.contract(abi=abi, address=self.prepare_address(contract_address))
        function = getattr(SmartContract.functions, fn_name)
        fn = function(*params)
        bytecode = fn._encode_transaction_data()

        data["to"] = add_0lt(contract_address)
        data["data"] = bytecode_to_bytes(bytecode)
        params = prepare_payload(data, self._private_key)
        if "nonce" not in params:
            params["nonce"] = await self.get_nonce(params["from"])

        resp = await self._rpc_call('query.EVMCall', params)
        if resp.get('error'):
            raise ProtocolAPIError(f"Failed to call smart contract method '{fn_name}' on address '{add_0lt(contract_address)}', details: {resp['error']['message']}")

        result = resp['result']['result']
        assert result, f'Call returned empty data, error occured during call "{fn_name}" method on contract "{contract_address}" with data {data}'
        return parse_call_response(fn.abi, result)
