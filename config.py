import os
from dotenv import load_dotenv

load_dotenv()

FEE_ADDRESS = os.environ['FEE_ADDRESS']
DEPLOYER_PK = os.environ['DEPLOYER_PK']
NODE_URL = os.environ.get('NODE_URL', 'http://127.0.0.1:26602/jsonrpc')
