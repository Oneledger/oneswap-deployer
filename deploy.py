import binascii
import time
import hashlib
import base64
import asyncio
import aiohttp
import ujson

from web3 import Web3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from config import (
    FEE_ADDRESS,
    DEPLOYER_PK,
    NODE_URL,
)


w3 = Web3()

try:
    with open('deployment.json', 'r', encoding='utf-8') as f:
        deployment = ujson.load(f)
        print('Deployment loaded')
except Exception:
    print('Deployment not found, skip')
    deployment = {}


def update_file_config():
    with open('deployment.json', 'w', encoding='utf-8') as f:
        ujson.dump(deployment, f, ensure_ascii=False, indent=8)


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
    if not prefix:
        return get_address_bytes(public_bytes)
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
    data = data.copy()
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


async def get_evm_account(address):
    resp = await rpc_call('query.EVMAccount', {
        "address": address,
        "blockTag": "latest",
    })
    return resp["result"]


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


async def check_balance(pk):
    address = get_address_from_private_key(pk)
    resp = await rpc_call('query.Balance', {"address": address})
    balance = float(resp['result']['balance'].split('OLT')[0].strip())
    if not balance:
        raise ValueError(f"Balance is not set for address {address}")
    print(f"Current balance: {balance}")


def parse_events(events):
    data = {}
    for event in events:
        for attribute in event['attributes']:
            key = base64.b64decode(attribute['key']).decode()
            value = attribute['value']
            if value:
                try:
                    value = base64.b64decode(attribute['value']).decode()
                except UnicodeDecodeError:
                    value = binascii.hexlify(base64.b64decode(attribute['value'])).decode()
            data[key] = value
    return data


async def wait_for_tx(tx_hash, repeat_count=25):
    error = None
    while repeat_count:
        resp = await rpc_call('query.Tx', {
            "hash": tx_hash,
            "prove": True,
        })
        error = resp.get('error')
        if not error:
            break

        print(f"Transaction '{tx_hash}' not mined, waiting...")
        await asyncio.sleep(1)
        repeat_count -= 1

    if error:
        raise ValueError(f"Failed to broadcast tx ({tx_hash}), details: {error['message']}")

    tx_result = resp['result']['result']['tx_result']
    print(f'Gas used: {tx_result["gasUsed"]}')
    print(f'Gas wanted: {tx_result["gasWanted"]}')
    if tx_result['code'] == 1:
        raise ValueError(f"Failed to execute tx ({tx_hash}), details: {tx_result['log']}")

    events = parse_events(tx_result['events'])
    if events.get('tx.error'):
        raise ValueError(f"Failed to execute vm ({tx_hash}), details: {events['tx.error']}")
    return events


def bytecode_to_bytes(bytecode):
    if bytecode.startswith('0x'):
        bytecode = bytecode[2:]
    return base64.b64encode(binascii.unhexlify(bytecode)).decode()


def get_contract_data(name):
    with open(f'./build/contracts/{name}.json') as contract_file:
        contract = ujson.load(contract_file)

    abi = contract['abi']
    bytecode = contract['bytecode']

    return abi, bytecode


async def execute_smart_contract(payload, pk):
    params = prepare_payload(payload, pk)
    evm_account = await get_evm_account(params["from"])
    if "nonce" not in params:
        params["nonce"] = evm_account["nonce"]

    print("Creating raw transaction")
    resp = await rpc_call('tx.CreateRawSend', params)
    if resp.get('error'):
        raise ValueError(f"Failed to broadcast tx, details: {resp['error']['message']}")

    raw_tx = resp["result"]["rawTx"]
    print(f"Raw created: {raw_tx[:20]}")

    print(f"Transaction signed by '{params['from']}'")
    resp = await rpc_call('broadcast.TxSync', get_signed_params(raw_tx, pk))
    transaction_hash = resp['result']['txHash']
    print(f"Transaction broadcasted, hash: {transaction_hash}")
    result = await wait_for_tx(transaction_hash)
    contract_address = result.get('tx.contract')
    if contract_address:
        return contract_address, transaction_hash

    return bool(binascii.hexlify(result['tx.status'].encode()).decode().rstrip('0')), transaction_hash


async def deploy_factory(fee_address):
    abi, bytecode = get_contract_data('UniswapV2Factory')

    UniswapV2Factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = UniswapV2Factory.constructor(Web3.toChecksumAddress(fee_address))
    return await execute_smart_contract({
        "amount": 0,
        "gas": 5000000,
        "gasPrice": 1,
        "data": bytecode_to_bytes(constructor.data_in_transaction),
    }, DEPLOYER_PK)


async def deploy_wolt():
    """Deployment of WOLT (Wrapped OneLedger Token)
    """
    abi, bytecode = get_contract_data('WOLT')

    ERC20 = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = ERC20.constructor()
    return await execute_smart_contract({
        "amount": 0,
        "gas": 5000000,
        "gasPrice": 1,
        "data": bytecode_to_bytes(constructor.data_in_transaction),
    }, DEPLOYER_PK)


async def deploy_dai():
    """Deployment of DAI (Dai Stablecoin)
    """
    abi, bytecode = get_contract_data('DAI')

    ERC20 = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = ERC20.constructor()
    return await execute_smart_contract({
        "amount": 0,
        "gas": 5000000,
        "gasPrice": 1,
        "data": bytecode_to_bytes(constructor.data_in_transaction),
    }, DEPLOYER_PK)


async def deploy_router(factory_address, weth_address):
    abi, bytecode = get_contract_data('UniswapV2Router')

    UniswapV2Router = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = UniswapV2Router.constructor(
        Web3.toChecksumAddress(factory_address),
        Web3.toChecksumAddress(weth_address),
    )
    return await execute_smart_contract({
        "amount": 0,
        "gas": 5000000,
        "gasPrice": 1,
        "data": bytecode_to_bytes(constructor.data_in_transaction),
    }, DEPLOYER_PK)


async def execute_method(contract_address, abi_name, fn_name, params, data):
    abi, _ = get_contract_data(abi_name)

    SmartContract = w3.eth.contract(abi=abi, address=Web3.toChecksumAddress(contract_address))
    function = getattr(SmartContract.functions, fn_name)
    fn = function(*params)
    bytecode = fn._encode_transaction_data()

    data["to"] = f'0lt{contract_address}'
    data["data"] = bytecode_to_bytes(bytecode)
    return await execute_smart_contract(data, DEPLOYER_PK)


async def call_method(contract_address, abi_name, fn_name, params, data, skip_error=False):
    abi, _ = get_contract_data(abi_name)

    SmartContract = w3.eth.contract(abi=abi, address=Web3.toChecksumAddress(contract_address))
    function = getattr(SmartContract.functions, fn_name)
    fn = function(*params)
    bytecode = fn._encode_transaction_data()

    data["to"] = f'0lt{contract_address}'
    data["data"] = bytecode_to_bytes(bytecode)
    params = prepare_payload(data, DEPLOYER_PK)
    evm_account = await get_evm_account(params["from"])
    if "nonce" not in params:
        params["nonce"] = evm_account["nonce"]

    resp = await rpc_call('query.EVMCall', params)
    if resp.get('error'):
        if skip_error:
            return resp
        raise ValueError(f"Failed to call smart contract method '{fn_name}' on address '0lt{contract_address}', details: {resp['error']['message']}")

    return resp['result']['result']


async def smart_deploy(address_key, func, params=None):
    if params is None:
        params = []

    if address_key in deployment:
        print(f"{address_key}: get address from previous deployment")
        address = deployment[address_key]["address"]
    else:
        print(f"{address_key}: deploying...")
        address, tx_hash = await func(*params)
        deployment[address_key] = {
            "address": address,
            "tx_hash": tx_hash,
        }
        update_file_config()

        print(f"{address_key}: done, address: '{address}'")
    print('')
    return address


async def check_is_approved_or_approve(contract_address, user_address, abi_name):
    deployer_address = get_address_from_private_key(DEPLOYER_PK, None)
    uint_max_value = int(0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

    data = {
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }

    print(f"Check address on allowance '0lt{user_address}' for {abi_name}...")
    result = await call_method(contract_address, abi_name, 'allowance', [
        Web3.toChecksumAddress(deployer_address),
        Web3.toChecksumAddress(user_address),
    ], data)
    allowance = int(result, 16)
    if not allowance:
        print(f"Approving address '0lt{user_address}' for {abi_name}...")
        done, _ = await execute_method(contract_address, abi_name, 'approve', [
            Web3.toChecksumAddress(user_address),
            uint_max_value,
        ], data)
        assert done is True, "WOLT not approved"
        print(f"{abi_name} approved for '{user_address}'")
    else:
        print(f"Allready allowance set for address '0lt{user_address}'")
    print('')


async def swap_olt_to_wolt(contract_address, user_address, amount):
    call_data = {
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }

    amount_in_wei = amount * 10 ** 18

    print(f"Checking address '0lt{user_address}' on initial balance of WOLT...")
    result = await call_method(contract_address, 'WOLT', 'balanceOf', [
        Web3.toChecksumAddress(user_address),
    ], call_data)

    balance_of =  int(result, 16)
    if balance_of < amount_in_wei:
        if not balance_of:
            delta_wei = amount_in_wei
            print(f"Balance not found, swaping OLT to WOLT...")
        else:
            delta_wei = amount_in_wei - balance_of
            print(f"Not enough balance for the next steps (required: {amount}, on balance: {balance_of // 10 ** 18}), adding delta {delta_wei // 10 ** 18} for swap")
            amount = delta_wei // 10 ** 18

        data = call_data.copy()
        data["amount"] = amount
        done, _ = await execute_method(contract_address, 'WOLT', 'deposit', [], data)
        assert done is True, "WOLT not exchanged"

        result = await call_method(contract_address, 'WOLT', 'balanceOf', [
            Web3.toChecksumAddress(user_address),
        ], call_data)
        balance_of = int(result, 16)
        assert balance_of >= amount_in_wei, "Initial balance not set, revert"
        print(f"{delta_wei} tokens was moved from OLT to WOLT for an address '0lt{user_address}'")
    else:
        print(f"Balance of '0lt{user_address}' filled, skipping...")
    print('')


async def mint_initial_dai_supply(contract_address, user_address, amount):
    call_data = {
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }

    amount_in_wei = amount * 10 ** 18

    print(f"Checking address '0lt{user_address}' on initial balance of DAI...")
    result = await call_method(contract_address, 'DAI', 'totalSupply', [], call_data)

    balance_of =  int(result, 16)
    if not balance_of:
        print(f"Balance not found, minting DAI...")

        done, _ = await execute_method(contract_address, 'DAI', 'mint', [
            Web3.toChecksumAddress(user_address),
            amount_in_wei,
        ], call_data)
        assert done is True, "DAI not minted"

        result = await call_method(contract_address, 'DAI', 'totalSupply', [], call_data)
        balance_of = int(result, 16)
        assert balance_of >= amount, "Initial supply not set, revert"
        print(f"{amount_in_wei} tokens was minted and add for an address '0lt{user_address}'")
    else:
        print(f"Balance of '0lt{user_address}' filled, skipping...")
    print('')


async def get_reserves(factory_address, token0, token1):
    call_data = {
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }
    result = await call_method(factory_address, 'UniswapV2Factory', 'getPair', [
        Web3.toChecksumAddress(token0),
        Web3.toChecksumAddress(token1),
    ], call_data)
    if not result.rstrip('0'):
        return [0, 0]

    pair_address = result[-40:]
    deployment["UniswapV2PairWOLT2DAI"] = {
        "address": pair_address,
        "tx_hash": deployment.get("UniswapV2PairWOLT2DAI", {}).get("tx_hash"),
    }
    update_file_config()

    result = await call_method(pair_address, 'UniswapV2Pair', 'getReserves', [], call_data)
    if not result.rstrip('0'):
        return [0, 0]
    
    return [int(result[:64], 16), int(result[64:128], 16)]


async def add_default_liquidity_with_eth(factory_address, router_address, user_address, token0, token1, amount0, amount1, deadline=300):
    reserves = await get_reserves(factory_address, token0, token1)
    if reserves[0] == 0 and reserves[1] == 0:
        call_data = {
            "amount": amount0,
            "gas": 10_000_000,
            "gasPrice": 1,
        }
        print(f'Adding the liquidity on router "0lt{router_address}" for ["0lt{token0}", "0lt{token1}"] with amounts - [{int(amount0 * 10 ** 18)}, {int(amount1 * 10 ** 18)}] ')

        done, tx_hash = await execute_method(router_address, 'UniswapV2Router', 'addLiquidityETH', [
            Web3.toChecksumAddress(token1),
            int(amount1 * 10 ** 18),
            0,
            0,
            Web3.toChecksumAddress(user_address),
            int(time.time() + deadline),
        ], call_data)
        assert done is True, f"Liquidity pair failed, please check '{tx_hash}' for more details"

        deployment["UniswapV2PairWOLT2DAI"] = {
            "address": deployment.get("UniswapV2PairWOLT2DAI", {}).get("address"),
            "tx_hash": tx_hash,
        }
        update_file_config()

        reserves = await get_reserves(factory_address, token0, token1)
        assert reserves[0] != 0, "reserve0 is zero"
        assert reserves[1] != 0, "reserve1 is zero"
        print(f'Liquidity filled.')
    else:
        print(f'Liquidity already filled for pair ["0lt{token0}", "0lt{token1}"] - reserve0: {reserves[0]}, reserve1: {reserves[1]}')
    print('')


async def main():
    deployer_address = get_address_from_private_key(DEPLOYER_PK, None)

    print(f'Launching deployment on node: {NODE_URL}\nWith deployer: {deployer_address}\n')
    print("Checking balance before start...")
    await check_balance(DEPLOYER_PK)
    print('')

    wolt_address = await smart_deploy(
        "WOLT",
        deploy_wolt,
    )

    dai_address = await smart_deploy(
        "DAI",
        deploy_dai,
    )

    factory_address = await smart_deploy(
        "UniswapV2Factory",
        deploy_factory,
        [FEE_ADDRESS],
    )

    router_address = await smart_deploy(
        "UniswapV2Router",
        deploy_router,
        [factory_address, wolt_address],
    )

    await check_is_approved_or_approve(wolt_address, router_address, 'WOLT')
    await check_is_approved_or_approve(dai_address, router_address, 'DAI')

    await swap_olt_to_wolt(wolt_address, deployer_address, 1_000_000)
    await mint_initial_dai_supply(dai_address, deployer_address, 21770)

    # NOTE: One time action, if the K is wrong, need to redeploy the uniswap
    await add_default_liquidity_with_eth(
        factory_address,
        router_address,
        deployer_address,
        wolt_address,
        dai_address,
        1_000_000,
        21770,
    )

    print('Done! Initial setup set successfully.')


if __name__ == '__main__':
    asyncio.run(main())
