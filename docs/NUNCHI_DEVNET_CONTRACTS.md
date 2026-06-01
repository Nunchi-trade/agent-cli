# Nunchi Devnet Contracts

The stable devnet RPC is:

```bash
https://nunchi-devnet.nunchi.trade
```

The latest deployed contract manifest lives at:

```bash
configs/nunchi_devnet_contracts.json
```

It includes the ERC-8004 passport/agent stack plus the exchange-side mock/oracle
contracts, `AccountNFT`, and `PerpOrderbook` used by the current devnet.

`ClearingHouse` and `MarketRegistry` remain deferred because the current devnet
still uses the same placeholder CH/MR pattern as prior devnets while the Cannon
deployment path is blocked on RPC compatibility.
