import click

from oneswap_deployer import config
from oneswap_deployer.client import Client, ProtocolAPIError
from oneswap_deployer.uniswap import UniswapManager
from oneswap_deployer.utils import coro, to_wei


@click.group()
@click.pass_context
def cli(ctx):
    """CLI for OneSwap deploy."""
    ctx.ensure_object(dict)

    click.echo(f'Launching deployment on node: {config.NODE_URL}')
    client = Client(config.NODE_URL, config.DEPLOYER_PK)

    umanager = UniswapManager(config.STATE_FILE)
    # loading cached state
    umanager.load_state()
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
    await umanager.test_deploy(token_rate, to_wei(initial_liquidity_count), to_wei(dai_supply))


@cli.command(name='balance')
@click.option('--token', type=str, help='ERC20 token')
@click.option('--address', type=str, help='Address')
@click.pass_context
@coro
async def balance(ctx, token, address):
    """Get current balance on the token
    """
    umanager = ctx.obj['umanager']
    balance = await umanager.get_token_balance(token, address)
    name = await umanager.get_token_name(token)

    click.echo(f'Current balance on "{token}":')
    click.echo(f' - {balance} {name}')


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
    if not reserves[0] and not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    token0, token1 = umanager.sort_tokens(token0, token1)

    name0 = await umanager.get_token_name(token0)
    name1 = await umanager.get_token_name(token1)
    
    click.echo(f'Current LP [{name0} - {name1}] info:')
    click.echo(f'Reserve {name0}: {reserves[0]}')
    click.echo(f'Reserve {name1}: {reserves[1]}')
    click.echo(f'{name0} -> {name1}: {reserves[1] / reserves[0]}')
    click.echo(f'{name1} -> {name0}: {reserves[0] / reserves[1]}')
    click.echo(f'K: {reserves[0] * reserves[1]}')


@cli.command(name='add_liquidity_OLT')
@click.option('--token', type=str, help='ERC20 token for LP with OLT')
@click.option('--amount_token_desired', type=int, help='Desired amount of tokens')
@click.option('--amount_token_min', default=0, show_default=True, type=int, help='Minimum amount of tokens')
@click.option('--amount_olt_min', default=0, show_default=True, type=int, help='Minimum amount of OLT')
@click.option('--to', type=str, help='Holder address')
@click.option('--deadline', type=int, default=300, show_default=True, help='Deadline for slippage')
@click.pass_context
@coro
async def add_liquidity_OLT(ctx, token, amount_token_desired, amount_token_min, amount_olt_min, to, deadline):
    """Used to add liquidity to the OLT - ERC20 pair
    """
    umanager = ctx.obj['umanager']
    reserves = await umanager.get_reserves(umanager.get_WOLT_address(), token)
    if not reserves[0] and not reserves[1]:
        click.secho(f'LP does not have initial reserve', fg='red')
        return

    K = reserves[0] * reserves[1]
    if umanager.get_WOLT_address() < token:
        amount_olt_desired = int((reserves[0] / reserves[1]) * amount_token_desired)
    else:
        amount_olt_desired = int((reserves[1] / reserves[0]) * amount_token_desired)

    await umanager.add_liquidity_OLT(
        token,
        amount_olt_desired,
        amount_token_desired,
        amount_olt_min,
        amount_token_min,
        deadline
    )


@cli.command(name='remove_liquidity_OLT')
@click.option('--token', type=str, help='ERC20 token for LP with OLT')
@click.option('--liquidity', type=int, help='Liquidity amount to withdraw')
@click.option('--amount_token_min', default=0, show_default=True, type=int, help='Minimum amount of tokens')
@click.option('--amount_olt_min', default=0, show_default=True, type=int, help='Minimum amount of OLT')
@click.option('--to', type=str, help='Recipient address')
@click.option('--deadline', type=int, default=300, show_default=True, help='Deadline for slippage')
@click.pass_context
@coro
async def remove_liquidity_OLT(ctx, token, liquidity, amount_token_min, amount_olt_min, to, deadline):
    """Used to remove liquidity to the OLT - ERC20 pair
    """
    umanager = ctx.obj['umanager']
    reserves = await umanager.get_reserves(umanager.get_WOLT_address(), token)
    if not reserves[0] and not reserves[1]:
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


if __name__ == '__main__':
    cli()
