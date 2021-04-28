import binascii
import hashlib
import base64
import asyncio
import aiohttp
import ujson

from web3 import Web3
from eth_utils import keccak
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from config import (
    FEE_ADDRESS,
    DEPLOYER_PK,
    NODE_URL,
)


w3 = Web3(Web3.EthereumTesterProvider())


def tx_sign(data, pk):
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(binascii.unhexlify(pk))
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_bytes, private_key.sign(base64.b64decode(data))


def get_address_bytes(public_bytes):
    return hashlib.sha256(public_bytes).hexdigest()[:40]


def get_address_from_private_key(pk, prefix='0lt'):
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(binascii.unhexlify(pk))
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f'{prefix}{get_address_bytes(public_bytes)}'


async def rpc_call(method, params):
    payload = {
        "method": method,
        "params": params,
        "id": 123,
        "jsonrpc": "2.0"
    }
    timeout = aiohttp.ClientTimeout(total=5)

    async with aiohttp.ClientSession(
        timeout=timeout,
        json_serialize=ujson.dumps
    ) as session:
        async with session.post(NODE_URL, json=payload, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        }) as response:
            return await response.json()


def prepare_payload(data, pk):
    if "from" not in data:
        data["from"] = get_address_from_private_key(pk)
    if "to" not in data:
        data["to"] = ""
    if "amount" in data:
        data["amount"] = {
            "currency": "OLT",
            "value": str(data["amount"] * 10 ** 18),
        }
    if "gasPrice" in data:
        data["gasPrice"] = {
            "currency": "OLT",
            "value": str(data["gasPrice"] * 10 ** 9), # something like gwei
        }
    return data


async def get_nonce(address):
    resp = await rpc_call('query.EVMAccount', {
        "from": address,
        "blockTag": "latest",
    })
    return resp["result"]["nonce"]


def get_signed_params(raw_tx, pk):
    public_key, signature = tx_sign(raw_tx, pk)
    return {
        "rawTx": raw_tx,
        "signature": base64.b64encode(signature).decode(),
        "publicKey": {
            "keyType": "ed25519",
            "data": base64.b64encode(public_key).decode(),
        },
    }


async def deploy_smart_contract(payload, pk):
    params = prepare_payload(payload, pk)
    if "nonce" not in params:
        params["nonce"] = await get_nonce(params["from"])

    print("Creating raw transaction")
    resp = await rpc_call('tx.CreateRawSend', params)
    raw_tx = resp["result"]["rawTx"]
    print(f"Raw created: {raw_tx[:20]}")

    print(f"Transaction signed by '{params['from']}'")
    resp = await rpc_call('broadcast.TxSync', get_signed_params(raw_tx, pk))
    import ipdb; ipdb.set_trace()


async def deploy_factory(fee_address):
    with open('./contracts/abi/uniswap_factory.json') as abi_file:
        abi = ujson.load(abi_file)

    with open('./contracts/bytecode/uniswap_factory.txt') as bytecode_file:
        bytecode = bytecode_file.read().strip()

    UniswapV2Factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = UniswapV2Factory.constructor(Web3.toChecksumAddress(fee_address))
    return await deploy_smart_contract({
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
        "data": constructor.data_in_transaction[2:],
    }, DEPLOYER_PK)


async def main():
    factory_address = await deploy_factory(FEE_ADDRESS)
    import ipdb; ipdb.set_trace()


if __name__ == '__main__':
    asyncio.run(main())
