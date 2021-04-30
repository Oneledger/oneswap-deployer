module.exports = {
    compilers: {
        solc: {
            version: '0.6.6',
            settings: {
                optimizer: {
                    enabled: true,
                    runs: 200,
                    details: {
                        yul: false,
                    }
                },
                evmVersion: 'istanbul',
            },
        },
    },
};
