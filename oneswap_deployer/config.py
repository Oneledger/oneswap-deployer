import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEE_ADDRESS = os.environ['FEE_ADDRESS']
DEPLOYER_PK = os.environ['DEPLOYER_PK']
NODE_URL = os.environ.get('NODE_URL', 'http://127.0.0.1:26602/jsonrpc')
STATE_FILE = os.environ.get('STATE_FILE', os.path.join(BASE_DIR, 'db/state.json'))
SWAP_LIST_FILE = os.environ.get('SWAP_LIST_FILE', os.path.join(BASE_DIR, 'db/default_swap_list.json'))
