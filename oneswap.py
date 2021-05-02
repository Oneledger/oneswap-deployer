from decimal import Decimal
import asyncio

import click

from oneswap_deployer import config
from oneswap_deployer.swaplist import SwapList
from oneswap_deployer.client import Client, ProtocolAPIError
from oneswap_deployer.uniswap import UniswapManager, UniswapUtils
from oneswap_deployer.utils import (
    coro, to_wei, remove_0lt, remove_0x, pretty_float,
    get_address_from_private_key,
)


def validate_address(ctx, param, value):
    if len(value) not in (40, 43):
        raise click.BadParameter('Wrong 0lt address')
    
    if len(value) == 43 and not value.startswith('0lt'):
        raise click.BadParameter('Wrong 0lt address')

    umanager = ctx.obj['umanager']

    try:
        address = remove_0x(umanager.client.prepare_address(remove_0lt(value)))
    except Exception:
        raise click.BadParameter('Wrong 0lt address')
    return address


def validate_swap_list(ctx, param, value):
    umanager = ctx.obj['umanager']

    if value != 'OLT':
        umanager.sl.get(value)

    return value


def validate_slippage(ctx, param, value):
    if value > 100:
        raise click.BadParameter('Wrong slippage percentage (must be less then 100)')
    elif value < 0:
        raise click.BadParameter('Wrong slippage percentage (must be more greter then 0)')
    return value


@click.group()
@click.pass_context
def cli(ctx):
    """CLI for OneSwap deploy."""
    ctx.ensure_object(dict)

    click.secho(f'Launching deployment on node: {config.NODE_URL}', fg='blue')
    client = Client(config.NODE_URL, config.DEPLOYER_PK)

    swap_list = SwapList.get_or_create(config.SWAP_LIST_FILE)

    umanager = UniswapManager(config.STATE_FILE)
    # loading cached state
    umanager.load_state()
    # add swap list to manager
    umanager.use_swap_list(swap_list)
    # embeding client
    umanager.use_client(client)
    # settings factory fee
    umanager.set_fee(config.FEE_ADDRESS)

    ctx.obj['umanager'] = umanager


@cli.command(name='test_deploy')
@click.option('--token_rate', default='0.02177', type=str, show_default=True, help='Current token ratio')
@click.option('--initial_liquidity_count', default=1_000_000, type=int, show_default=True, help='OLT token count for the first liquidity pair')
@click.option('--dai_supply', default=50_000, type=int, show_default=True, help='DAI token count for the first initial supply')
@click.pass_context
@coro
async def test_deploy(ctx, token_rate, initial_liquidity_count, dai_supply):
    """Test deployment for OLT - DAI liquidity pair
    """
    umanager = ctx.obj['umanager']

    try:
        await umanager.test_deploy(token_rate, to_wei(initial_liquidity_count), to_wei(dai_supply))
    except ProtocolAPIError as e:
        click.secho(e.args[0], fg='red')


@cli.command(name='balance')
@click.option('--address', type=str, callback=validate_address, help='Address')
@click.pass_context
@coro
async def balance(ctx, address):
    """Get current balance on the token
    """
    umanager = ctx.obj['umanager']
    balances = []
    balances.append({
        'balance': await umanager.client.get_balance(address),
        'name': 'OLT',
        'decimals': 18,
    })

    swap_list = umanager.sl.to_list()

    for data in swap_list:
        name = data['name']
        token_address = data['address']
        balance, decimals = await asyncio.gather(
            umanager.get_token_balance(token_address, address),
            umanager.get_token_decimals(token_address),
        )
        balances.append({
            'balance': balance,
            'name': name,
            'decimals': decimals,
        })

    click.secho(f'\nCurrent balances: \n', fg='green')
    for balance in balances:
        click.secho(f' * {balance["name"]}: {pretty_float(balance["balance"], balance["decimals"])}', fg='cyan')


@cli.command(name='lp_info')
@click.option('--token0', type=str, help='ERC20 token for 1 pair')
@click.option('--token1', type=str, help='ERC20 token for 2 pair')
@click.pass_context
@coro
async def lp_info(ctx, token0, token1):
    """Get current info for LP pair
    """
    umanager = ctx.obj['umanager']
    reserves = await umanager.get_reserves(token0, token1)
    if not reserves[0] or not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    token0, token1 = UniswapUtils.sort_tokens(token0, token1)

    name0 = await umanager.get_token_name(token0)
    name1 = await umanager.get_token_name(token1)

    fee_rate = await umanager.get_pool_fee_rate()
    
    click.echo(f'Current LP [{name0} - {name1}] info:')
    click.echo(f'Reserve {name0}: {reserves[0]}')
    click.echo(f'Reserve {name1}: {reserves[1]}')
    click.echo(f'{name0} -> {name1}: {reserves[1] / reserves[0]}')
    click.echo(f'{name1} -> {name0}: {reserves[0] / reserves[1]}')
    click.echo(f'Liquidity: {reserves[0] * reserves[1]}')
    click.echo(f'Pool fee rate (%): {pretty_float(fee_rate * 100, 0)}')


@cli.command(name='approve')
@click.option('--token', type=str, help='ERC20 token where will be an approval')
@click.option('--user_address', type=str, help='Address for allowance')
@click.option('--wad', default=UniswapManager.MAX_UINT_VALUE, show_default=True, type=int, help='Amount of tokens to allow')
@click.pass_context
@coro
async def approve(ctx, token, user_address, wad):
    """Used to add allowance on specific contract
    """
    umanager = ctx.obj['umanager']

    try:
        await umanager.erc20_approve(token, user_address, wad)
    except ProtocolAPIError as e:
        click.secho(e.args[0], fg='red')


@cli.command(name='add_liquidity_OLT')
@click.option('--token', type=str, help='ERC20 token for LP with OLT')
@click.option('--amount_token_desired', type=int, help='Desired amount of tokens')
@click.option('--amount_token_min', default=0, show_default=True, type=int, help='Minimum amount of tokens')
@click.option('--amount_olt_min', default=0, show_default=True, type=int, help='Minimum amount of OLT')
@click.option('--to', type=str, callback=validate_address, default=get_address_from_private_key(config.DEPLOYER_PK, None), show_default=True, help='Recipient address')
@click.option('--deadline', type=int, default=300, show_default=True, help='Deadline for the transaction period')
@click.pass_context
@coro
async def add_liquidity_OLT(ctx, token, amount_token_desired, amount_token_min, amount_olt_min, to, deadline):
    """Used to add liquidity to the OLT - ERC20 pair
    """
    umanager = ctx.obj['umanager']
    reserves = await umanager.get_reserves(umanager.get_WOLT_address(), token)
    if not reserves[0] or not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    K = reserves[0] * reserves[1]
    if umanager.get_WOLT_address() < token:
        amount_olt_desired = int((reserves[0] / reserves[1]) * amount_token_desired)
    else:
        amount_olt_desired = int((reserves[1] / reserves[0]) * amount_token_desired)

    try:
        await umanager.add_liquidity_OLT(
            token,
            amount_olt_desired,
            amount_token_desired,
            amount_olt_min,
            amount_token_min,
            deadline
        )
    except ProtocolAPIError as e:
        click.secho(e.args[0], fg='red')


@cli.command(name='remove_liquidity_OLT')
@click.option('--token', type=str, help='ERC20 token for LP with OLT')
@click.option('--liquidity', type=int, help='Liquidity amount to withdraw')
@click.option('--amount_token_min', default=0, show_default=True, type=int, help='Minimum amount of tokens')
@click.option('--amount_olt_min', default=0, show_default=True, type=int, help='Minimum amount of OLT')
@click.option('--to', type=str, callback=validate_address, default=get_address_from_private_key(config.DEPLOYER_PK, None), show_default=True, help='Recipient address')
@click.option('--deadline', type=int, default=300, show_default=True, help='Deadline for the transaction period')
@click.pass_context
@coro
async def remove_liquidity_OLT(ctx, token, liquidity, amount_token_min, amount_olt_min, to, deadline):
    """Used to remove liquidity to the OLT - ERC20 pair
    """
    umanager = ctx.obj['umanager']
    reserves = await umanager.get_reserves(umanager.get_WOLT_address(), token)
    if not reserves[0] or not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    try:
        await umanager.remove_liquidity_OLT(
            token,
            liquidity,
            amount_olt_min,
            amount_token_min,
            deadline
        )
    except ProtocolAPIError as e:
        click.secho(e.args[0], fg='red')


@cli.command(name='swap')
@click.option('--amount', type=Decimal, help='Amount of tokens to swap')
@click.option('--direction', type=click.Choice(['IN', 'OUT'], case_sensitive=False), help='IN/OUT direction for amount (default: IN)')
@click.option('--name0', type=str, callback=validate_swap_list, help='ERC20 token name where to withdraw')
@click.option('--name1', type=str, callback=validate_swap_list, help='ERC20 token name where to receive')
@click.option('--to', type=str, callback=validate_address, default=get_address_from_private_key(config.DEPLOYER_PK, None), show_default=True, help='Recipient address')
@click.option('--deadline', type=int, default=300, show_default=True, help='Deadline for the transaction period')
@click.option('--slippage', type=Decimal, default=0.5, callback=validate_slippage, show_default=True, help='Slippage percentage for the amount')
@click.option('--auto_confirm', type=bool, default=False, show_default=True, help='Autoconfirm the swap without pre-send screen')
@click.pass_context
@coro
async def swap(ctx, amount, direction, name0, name1, to, deadline, slippage, auto_confirm):
    """Performs a swap
    """
    umanager = ctx.obj['umanager']

    if not direction:
        direction = 'IN'

    if name0 == 'OLT':
        token0 = umanager.sl.get('WOLT')
        decimals0 = 18

        token1 = umanager.sl.get(name1)
        name1, decimals1 = await asyncio.gather(
            umanager.get_token_name(token1),
            umanager.get_token_decimals(token1),
        )
    elif name1 == 'OLT':
        token1 = umanager.sl.get('WOLT')
        decimals1 = 18

        token0 = umanager.sl.get(name0)
        decimals0 = await umanager.get_token_decimals(token0)
    else:
        token0 = umanager.sl.get(name0)
        token1 = umanager.sl.get(name1)
        decimals0, decimals1 = await asyncio.gather(
            umanager.get_token_decimals(token0),
            umanager.get_token_decimals(token1),
        )
        reserves = await umanager.get_reserves(token0, token1)

    fee_rate = await umanager.get_pool_fee_rate()

    reserves = await umanager.get_reserves(token0, token1)
    if not reserves[0] or not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    if direction == 'IN':
        amount_in_max, amount_out = await umanager.get_amounts_out(to_wei(amount), token0, token1)
        amount_out_min = UniswapUtils.calculate_min_slippage_amount(amount_out, slippage)
        if not amount_out_min:
            click.secho(f'Not enough tokens for the slippage ({slippage}). Try to descrese a slippage percentage', fg='red')
            return
        
        click.secho(f'\nSwap info:', fg='green')
        click.secho(f' * slippage tolerance: {slippage}', fg='magenta')
        click.secho(f' * input amount: {pretty_float(amount_in_max, decimals0)}', fg='magenta')
        click.secho(f' * minimum received: {pretty_float(amount_out_min, decimals1)}', fg='magenta')
        click.secho(f' * liquidity provider fee: {pretty_float(amount_in_max * fee_rate, decimals0)}', fg='magenta')
    
    elif direction == 'OUT':
        amount_in, amount_out_min = await umanager.get_amounts_in(to_wei(amount), token0, token1)
        amount_in_max = UniswapUtils.calculate_max_slippage_amount(amount_in, slippage)

        click.secho(f'\nSwap info:', fg='green')
        click.secho(f' * slippage tolerance: {slippage}', fg='magenta')
        click.secho(f' * maximum sold: {pretty_float(amount_in_max, decimals1)}', fg='magenta')
        click.secho(f' * output amount: {pretty_float(amount_out_min, decimals0)}', fg='magenta')
        click.secho(f' * liquidity provider fee: {pretty_float(amount_in * fee_rate, decimals0)}', fg='magenta')

    if not auto_confirm and not click.confirm('\nDo you want to confirm swap?'):
        click.secho('Swap aborted.', fg='red')
        return

    try:
        if name0 == 'OLT':
            await umanager.swap_exact_OLT_for_tokens(token1, amount_in_max, amount_out_min, to, deadline)
        elif name1 == 'OLT':
            await umanager.swap_tokens_for_exact_OLT(token0, amount_in_max, amount_out_min, to, deadline)
        else:
            if direction == 'OUT':
                await umanager.swap_tokens_for_exact_tokens(token0, token1, amount_in_max, amount_out_min, to, deadline)
            elif direction == 'IN':
                await umanager.swap_exact_tokens_for_tokens(token1, token0, amount_in_max, amount_out_min, to, deadline)
                
    except ProtocolAPIError as e:
        click.secho(e.args[0], fg='red')

if __name__ == '__main__':
    cli()
