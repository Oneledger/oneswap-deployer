import click

from oneswap_deployer import config
from oneswap_deployer.client import Client
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
    
    click.echo(f'\nCurrent LP {token0} to {token1} info:')
    click.echo(f'{token0} balance: {reserves[0]}')
    click.echo(f'{token1} balance: {reserves[1]}')
    click.echo(f'Ratio: {reserves[0] / reserves[1]}')
    click.echo(f'K: {reserves[0] * reserves[1]}')
    click.echo('')


@cli.command(name='add_liquidity_OLT')
@click.option('--token', type=str, help='ERC20 token for LP with OLT')
@click.option('--amount_token_desired', type=str, help='Desired amount of tokens')
@click.option('--amount_token_min', default='0', show_default=True, type=str, help='Minimum amount of tokens')
@click.option('--amount_olt_min', default='0', show_default=True, type=str, help='Minimum amount of OLT')
@click.option('--to', type=str, help='Address for LP')
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
    amount_olt_desired = (reserves[0] / reserves[1]) * float(amount_token_desired)

    await umanager.add_liquidity_OLT(
        token,
        to_wei(amount_olt_desired),
        to_wei(amount_token_desired),
        to_wei(amount_olt_min),
        to_wei(amount_token_min),
        deadline
    )


if __name__ == '__main__':
    cli()
