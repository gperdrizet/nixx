# Phone access setup

Access nixx from your phone (or laptop) via SSH over Tailscale. Full TUI support.

## How it works

```
Phone (Termius) → Tailscale VPN → SSH (port 4444) → pyrite → nixx chat
```

Tailscale creates a WireGuard-based mesh VPN between your devices. Traffic is peer-to-peer and end-to-end encrypted. Tailscale's coordination servers handle key exchange and NAT traversal but never see your data.

## Prerequisites

- Tailscale account (free tier is fine)
- Tailscale installed on pyrite: `curl -fsSL https://tailscale.com/install.sh | sh`
- Tailscale app on phone (Play Store / App Store)
- SSH client on phone

## Phone setup

1. Install **Tailscale** from the Play Store and log in
2. Install **Termius** (connection manager) and **Termux** (terminal) from the Play Store
3. Generate an SSH key in Termux: `ssh-keygen -t ed25519`
4. Copy the key to pyrite (requires temporarily enabling password auth):
   ```bash
   # On pyrite: enable password auth
   sudo sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
   sudo systemctl restart ssh

   # On phone (Termux): copy the key
   ssh-copy-id -p 4444 siderealyear@100.80.177.16

   # On pyrite: disable password auth
   sudo sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
   sudo systemctl restart ssh
   ```
5. In Termius, create a connection: host `100.80.177.16`, port `4444`, user `siderealyear`, select the key generated in step 3

## Usage

From Termius, connect to pyrite and run:
```bash
nixx chat
```

All slash commands work: `/sources`, `/lookup`, `/source`, `/context`.

## Troubleshooting

- **Connection refused**: Check SSH is running (`systemctl status ssh`) and you have the right port (4444)
- **Permission denied**: Key not copied correctly. Re-run `ssh-copy-id` from Termux
- **nixx: command not found**: Install via pipx: `pipx install --editable ~/nixx`
- **"All connection attempts failed"**: nixx server is not running. `sudo systemctl start nixx-server`
