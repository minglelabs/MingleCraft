# Remote testing from a Mac

For the first remote setup, do not move the MingleCraft server to the Mac. Keep these components together on the home Windows machine:

```text
StarCraft 1.16.1 + BWAPI + MingleCraft.dll + Python MingleCraft server
```

Use the Mac only as a remote desktop client. This keeps the native game, the BWAPI game-thread calls and the loopback bridge on one machine. The OpenRouter request then leaves the Windows machine from the Python process.

## Recommended network layout

1. Install Tailscale on the home Windows PC and the company Mac, and sign both into the same tailnet.
2. Keep the Windows PC awake, connected to power and set to stay available for remote connections.
3. If the Windows edition is Pro, Enterprise, Education or Windows Server, enable Windows Remote Desktop and keep Network Level Authentication enabled. Windows Home can connect as an RDP client but cannot host incoming Microsoft Remote Desktop connections.
4. In the Mac Windows App client, connect to the Windows PC's Tailscale `100.x.y.z` address or MagicDNS name. Tailscale provides the private path; do not port-forward RDP or MingleCraft's port `8765` from the home router.
5. On the Windows desktop, start the MingleCraft server locally:

   ```powershell
   cd C:\path\to\MingleCraft
   .\.venv\Scripts\Activate.ps1
   $env:OPENROUTER_API_KEY = "your-key"
   minglecraft serve --provider openrouter-jev --model "~typesafe/jev-latest" --map "(2)Destination.scx"
   ```

6. Start Chaoslauncher and the match from that same Windows desktop. The Mac session is only controlling the desktop; BWAPI still calls the local Python server at `127.0.0.1:8765`.

Tailscale's official Windows RDP guide supports connecting from macOS through the tailnet. Windows Home is not an RDP host, so use another trusted remote desktop product or upgrade the host edition if necessary. Keep the remote Windows account protected by a strong unique password and the Tailscale access policy.

## Why this stays separate

Remote desktop and game automation have different failure and security boundaries. MingleCraft should own observations, finite decisions, BWAPI commands and evaluation traces. A future companion project, `minglecraft-remote`, can provide a narrow Windows operator agent for:

- health checks and current match status;
- starting or stopping an allowlisted MingleCraft process;
- retrieving logs and summaries;
- optional screenshot capture.

That agent should never expose arbitrary shell execution or keyboard/mouse injection over the MingleCraft HTTP API. Full desktop control belongs to the authenticated remote desktop layer. The v0.1 repository does not include this companion agent yet.

## Troubleshooting

- If the Mac cannot connect, first verify both Tailscale clients show the other device as online and try the Windows PC's `100.x.y.z` address.
- If the desktop connects but the bot does not act, open PowerShell on Windows and run `Invoke-WebRequest http://127.0.0.1:8765/health`.
- If the health check fails, start `minglecraft serve` on Windows before launching the match.
- If the health check works but the game shows no actions, check `runs\` on Windows and confirm Chaoslauncher loaded `MingleCraft.dll`.
- Do not expose port `8765` or RDP directly to the public internet.
