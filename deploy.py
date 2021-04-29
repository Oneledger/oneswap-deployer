import binascii
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
        raise ValueError(f"Failed to broadcast tx, details: {error['message']}")

    tx_result = resp['result']['result']['tx_result']
    print(f'Gas used: {tx_result["gasUsed"]}')
    print(f'Gas wanted: {tx_result["gasWanted"]}')
    if tx_result['code'] == 1:
        raise ValueError(f"Failed to execute tx, details: {tx_result['log']}")

    events = parse_events(tx_result['events'])
    if events.get('tx.error'):
        raise ValueError(f"Failed to execute vm, details: {events['tx.error']}")
    return events


def bytecode_to_bytes(bytecode):
    if bytecode.startswith('0x'):
        bytecode = bytecode[2:]
    return base64.b64encode(binascii.unhexlify(bytecode)).decode()


def get_contract_data(name):
    with open(f'./contracts/abi/{name}.json') as abi_file:
        abi = ujson.load(abi_file)

    with open(f'./contracts/bytecode/{name}.txt') as bytecode_file:
        bytecode = bytecode_file.read().strip()
    
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


async def deploy_dai(chain_id=1):
    """Deployment of DAI (Dai Stablecoin)
    """
    abi, bytecode = get_contract_data('DAI')

    ERC20 = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = ERC20.constructor(chain_id)
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

    data["data"] = bytecode_to_bytes(bytecode)
    return await execute_smart_contract(data, DEPLOYER_PK)


async def call_method(contract_address, abi_name, fn_name, params, data):
    abi, _ = get_contract_data(abi_name)

    SmartContract = w3.eth.contract(abi=abi, address=Web3.toChecksumAddress(contract_address))
    function = getattr(SmartContract.functions, fn_name)
    fn = function(*params)
    bytecode = fn._encode_transaction_data()

    data["data"] = bytecode_to_bytes(bytecode)
    params = prepare_payload(data, DEPLOYER_PK)
    evm_account = await get_evm_account(params["from"])
    if "nonce" not in params:
        params["nonce"] = evm_account["nonce"]

    resp = await rpc_call('query.EVMCall', params)
    if resp.get('error'):
        raise ValueError(f"Failed to call smart contract method '{fn_name}' on address '{contract_address}', details: {resp['error']['message']}")

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
        with open('deployment.json', 'w', encoding='utf-8') as f:
            ujson.dump(deployment, f, ensure_ascii=False, indent=4)
        print(f"{address_key}: done, address: '{address}'")
    print('')
    return address


async def check_is_approved_or_approve(contract_address, user_address, abi_name):
    deployer_address = get_address_from_private_key(DEPLOYER_PK, None)
    uint_max_value = int(0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

    data = {
        "to": f"0lt{contract_address}",
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }

    print(f"Check address on allowance '0lt{user_address}' for {abi_name}...")
    result = await call_method(contract_address, abi_name, 'allowance', [
        Web3.toChecksumAddress(deployer_address),
        Web3.toChecksumAddress(user_address),
    ], data)
    if not result:
        print(f"Approving address '0lt{user_address}' for {abi_name}...")
        done = await execute_method(contract_address, abi_name, 'approve', [
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
        "to": f"0lt{contract_address}",
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
            delta_wei = balance_of
            print(f"Balance not found, swaping OLT to WOLT...")
        else:
            delta_wei = amount_in_wei - balance_of
            print(f"Not enough balance for the next steps (required: {amount}, on balance: {balance_of / 10 ** 18}), adding delta {delta_wei} for swap")
            amount = delta_wei / 10 ** 18

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
        "to": f"0lt{contract_address}",
        "amount": 0,
        "gas": 1000000,
        "gasPrice": 1,
    }

    print(f"Checking address '0lt{user_address}' on initial balance of DAI...")
    result = await call_method(contract_address, 'DAI', 'totalSupply', [], call_data)

    balance_of =  int(result, 16)
    if not balance_of:
        print(f"Balance not found, minting DAI...")

        done, _ = await execute_method(contract_address, 'DAI', 'mint', [
            Web3.toChecksumAddress(user_address),
            amount,
        ], call_data)
        assert done is True, "DAI not minted"

        result = await call_method(contract_address, 'DAI', 'totalSupply', [], call_data)
        balance_of = int(result, 16)
        assert balance_of >= amount, "Initial supply not set, revert"
        print(f"{amount} tokens was minted and add for an address '0lt{user_address}'")
    else:
        print(f"Balance of '0lt{user_address}' filled, skipping...")
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

    await check_is_approved_or_approve(wolt_address, deployer_address, 'WOLT')
    await check_is_approved_or_approve(dai_address, deployer_address, 'DAI')

    await swap_olt_to_wolt(wolt_address, deployer_address, 10)
    await mint_initial_dai_supply(dai_address, deployer_address, 10_000)

    # await add_default_liquidity_pair(
    #     wolt_address,
    #     dai_address,
    #     '10',
    #     '0.2177',
    #     300,  # 5 min deadline
    # )

    print('Done! Initial setup set successfully.')


if __name__ == '__main__':
    asyncio.run(main())
