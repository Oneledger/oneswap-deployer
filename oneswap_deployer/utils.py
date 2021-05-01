import binascii
import os
import time
import hashlib
import base64
import asyncio
import itertools
from functools import wraps

import aiohttp
import ujson
import web3
from web3 import Web3
from web3._utils.abi import get_abi_output_types, map_abi_data
from web3._utils.normalizers import BASE_RETURN_NORMALIZERS

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .config import BASE_DIR


def parse_call_response(fn_abi, result):
    output_types = get_abi_output_types(fn_abi)
    output_data = Web3().codec.decode_abi(get_abi_output_types(fn_abi), binascii.unhexlify(result))
    _normalizers = itertools.chain(
        BASE_RETURN_NORMALIZERS,
        [],
    )
    normalized_data = map_abi_data(_normalizers, output_types, output_data)
    if len(normalized_data) == 1:
        return normalized_data[0]
    else:
        return normalized_data


def add_0lt(address):
    if address.startswith('0lt'):
        return address
    return f'0lt{address}'


def remove_0x(address):
    if address.startswith('0x'):
        return address[2:]
    return address


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


def prepare_payload(data, pk=None):
    data = data.copy()
    if "from" not in data and pk:
        data["from"] = get_address_from_private_key(pk)
    if "to" not in data:
        data["to"] = ""
    return data


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


def bytecode_to_bytes(bytecode):
    if bytecode.startswith('0x'):
        bytecode = bytecode[2:]
    return base64.b64encode(binascii.unhexlify(bytecode)).decode()


def get_contract_data(name):
    with open(os.path.join(BASE_DIR, 'build/contracts', f'{name}.json')) as contract_file:
        contract = ujson.load(contract_file)

    abi = contract['abi']
    bytecode = contract['bytecode']

    return abi, bytecode

def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


def to_wei(value):
    return int(value * 10 ** 18)


def from_wei(value):
    return float(value / 10 ** 18)
