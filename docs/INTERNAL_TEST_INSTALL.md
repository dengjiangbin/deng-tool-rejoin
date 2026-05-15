# Internal test install (owner / testers)

This is **not** a public stable install. The moving **main-dev** build is served from a fixed HTTPS URL; download is still license-gated and only keys whose redeemed owner Discord ID appears in `LICENSE_OWNER_DISCORD_IDS` or `REJOIN_TESTER_DISCORD_IDS` may authorize the artifact.

```sh
curl -fsSL https://rejoin.deng.my.id/install/test/latest -o install.sh && bash install.sh
```

Optional alias: `GET /install/beta/latest` redirects to `/install/test/latest`.

Public beginners should continue to use `GET /install/latest` (stable only); see [NEW_USER_TERMUX_GUIDE.md](NEW_USER_TERMUX_GUIDE.md).
