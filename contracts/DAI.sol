pragma solidity =0.6.6;

import "@uniswap/v2-core/contracts/interfaces/IERC20.sol";
import "@uniswap/v2-periphery/contracts/libraries/SafeMath.sol";

contract LibNote {
    event LogNote(
        bytes4 indexed sig,
        address indexed usr,
        bytes32 indexed arg1,
        bytes32 indexed arg2,
        bytes data
    ) anonymous;

    modifier note {
        _;
        assembly {
            // log an 'anonymous' event with a constant 6 words of calldata
            // and four indexed topics: selector, caller, arg1 and arg2
            let mark := msize() // end of memory ensures zero
            mstore(0x40, add(mark, 288)) // update free memory pointer
            mstore(mark, 0x20) // bytes type data offset
            mstore(add(mark, 0x20), 224) // bytes size (padded)
            calldatacopy(add(mark, 0x40), 0, 224) // bytes payload
            log4(
                mark,
                288, // calldata
                shl(224, shr(224, calldataload(0))), // msg.sig
                caller(), // msg.sender
                calldataload(4), // arg1
                calldataload(36) // arg2
            )
        }
    }
}

contract DAI is LibNote, IERC20 {
    // --- Auth ---
    mapping(address => uint256) public wards;

    function rely(address guy) external note auth {
        wards[guy] = 1;
    }

    function deny(address guy) external note auth {
        wards[guy] = 0;
    }

    modifier auth {
        require(wards[msg.sender] == 1, "Dai/not-authorized");
        _;
    }

    // --- ERC20 Data ---
    string public constant override name = "Dai Stablecoin";
    string public constant override symbol = "DAI";
    string public constant version = "1";
    uint8 public constant override decimals = 18;
    uint256 public override totalSupply;

    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public override allowance;
    mapping(address => uint256) public nonces;

    event Approval(address indexed src, address indexed guy, uint256 wad);
    event Transfer(address indexed src, address indexed dst, uint256 wad);

    // --- EIP712 niceties ---
    bytes32 public DOMAIN_SEPARATOR;
    // bytes32 public constant PERMIT_TYPEHASH = keccak256("Permit(address holder,address spender,uint256 nonce,uint256 expiry,bool allowed)");
    bytes32 public constant PERMIT_TYPEHASH =
        0xea2aa0a1be11a07ed86d755c93467f4f82362b452371d1ba94d1715123511acb;

    constructor() public {
        uint256 chainId_;
        assembly {
            chainId_ := chainid()
        }

        wards[msg.sender] = 1;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                chainId_,
                address(this)
            )
        );
    }

    // --- Token ---
    function transfer(address dst, uint256 wad)
        external
        override
        returns (bool)
    {
        return transferFrom(msg.sender, dst, wad);
    }

    function transferFrom(
        address src,
        address dst,
        uint256 wad
    ) public override returns (bool) {
        require(balanceOf[src] >= wad, "Dai/insufficient-balance");
        if (src != msg.sender && allowance[src][msg.sender] != uint256(-1)) {
            require(
                allowance[src][msg.sender] >= wad,
                "Dai/insufficient-allowance"
            );
            allowance[src][msg.sender] = SafeMath.sub(
                allowance[src][msg.sender],
                wad
            );
        }
        balanceOf[src] = SafeMath.sub(balanceOf[src], wad);
        balanceOf[dst] = SafeMath.add(balanceOf[dst], wad);
        emit Transfer(src, dst, wad);
        return true;
    }

    function mint(address usr, uint256 wad) external auth {
        balanceOf[usr] = SafeMath.add(balanceOf[usr], wad);
        totalSupply = SafeMath.add(totalSupply, wad);
        emit Transfer(address(0), usr, wad);
    }

    function burn(address usr, uint256 wad) external {
        require(balanceOf[usr] >= wad, "Dai/insufficient-balance");
        if (usr != msg.sender && allowance[usr][msg.sender] != uint256(-1)) {
            require(
                allowance[usr][msg.sender] >= wad,
                "Dai/insufficient-allowance"
            );
            allowance[usr][msg.sender] = SafeMath.sub(
                allowance[usr][msg.sender],
                wad
            );
        }
        balanceOf[usr] = SafeMath.sub(balanceOf[usr], wad);
        totalSupply = SafeMath.sub(totalSupply, wad);
        emit Transfer(usr, address(0), wad);
    }

    function approve(address usr, uint256 wad)
        external
        override
        returns (bool)
    {
        allowance[msg.sender][usr] = wad;
        emit Approval(msg.sender, usr, wad);
        return true;
    }

    // --- Alias ---
    function push(address usr, uint256 wad) external {
        transferFrom(msg.sender, usr, wad);
    }

    function pull(address usr, uint256 wad) external {
        transferFrom(usr, msg.sender, wad);
    }

    function move(
        address src,
        address dst,
        uint256 wad
    ) external {
        transferFrom(src, dst, wad);
    }

    // --- Approve by signature ---
    function permit(
        address holder,
        address spender,
        uint256 nonce,
        uint256 expiry,
        bool allowed,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        bytes32 digest =
            keccak256(
                abi.encodePacked(
                    "\x19\x01",
                    DOMAIN_SEPARATOR,
                    keccak256(
                        abi.encode(
                            PERMIT_TYPEHASH,
                            holder,
                            spender,
                            nonce,
                            expiry,
                            allowed
                        )
                    )
                )
            );

        require(holder != address(0), "Dai/invalid-address-0");
        require(holder == ecrecover(digest, v, r, s), "Dai/invalid-permit");
        require(expiry == 0 || now <= expiry, "Dai/permit-expired");
        require(nonce == nonces[holder]++, "Dai/invalid-nonce");
        uint256 wad = allowed ? uint256(-1) : 0;
        allowance[holder][spender] = wad;
        emit Approval(holder, spender, wad);
    }
}
