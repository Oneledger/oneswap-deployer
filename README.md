# OneSwap deployer
Deployment scripts based on UniswapV2 to the OneLedger network

### Installation
1. `yarn`
2. `pip3 install -r requirements.txt`
3. `npm run compile`


### Building the OneSwap initial set up (WOLT + DAI)
The several steps will be executed in order to deploy the OneSwap (base on Uniswap) contracts:
1. Deployment UniswapV2Factory;
2. Deployment WOLT token (WETH9 based);
3. Deployment UniswapV2Router;

In order to have an initial liquidity, the next steps performed:
1. Deployment of ERC20 base token;
2. Adding liquidity with WOLT + ERC20 tokens to create UniswapV2Pair;

### Quick build of OneSwap with 1000000 WOLT - 21770 DAI LP
`npm run test_deploy`

Available commands to work with a pool colud be found via
`python3 oneswap.py --help`

Methods:
* `test_deploy` - initial test deployment to start work
* `lp_info` - liquidity pool info
* `add_liquidity_OLT` - adding the liquidity to [ERC20 token - OLT]
* `remove_liquidity_OLT` - removing the liquidity to [ERC20 token - OLT]
* `balance` - balance of an address on some token
