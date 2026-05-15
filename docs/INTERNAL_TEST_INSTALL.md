# Internal test install (owner / testers)

This is **not** a public stable install. The moving **main-dev** build is served from a fixed HTTPS URL; download is still license-gated and only keys whose redeemed owner Discord ID appears in `LICENSE_OWNER_DISCORD_IDS` or `REJOIN_TESTER_DISCORD_IDS` may authorize the artifact.

```sh
curl -fsSL https://rejoin.deng.my.id/install/test/latest -o install.sh && bash install.sh
```

Rebuild or refresh the tarball after code changes:

```sh
python scripts/build_internal_test_artifact.py
```

This writes `releases/main-dev/deng-tool-rejoin-main-dev.tar.gz`, updates `artifact_sha256` for **main-dev** in `data/rejoin_versions.json`, and must be run on the machine that serves `REJOIN_ARTIFACT_ROOT`.

Optional alias: `GET /install/beta/latest` redirects to `/install/test/latest`.

Public beginners should continue to use `GET /install/latest` (stable only); see [NEW_USER_TERMUX_GUIDE.md](NEW_USER_TERMUX_GUIDE.md).
