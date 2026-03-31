**Service: Run `sync_messages.py` via systemd**

Files added:
- [systemd/discord-to-slack-sync.service](systemd/discord-to-slack-sync.service)
- [scripts/install_service.sh](scripts/install_service.sh)
- [scripts/uninstall_service.sh](scripts/uninstall_service.sh)

Install (from the repo root):

```bash
chmod +x scripts/install_service.sh scripts/uninstall_service.sh
scripts/install_service.sh
```

`install_service.sh` will:
- Create a virtualenv at `./venv` (if missing).
- Install `requirements.txt` into the venv.
- Copy the systemd unit to `/etc/systemd/system` and enable+start the service.

Check status with:

```bash
sudo systemctl status discord-to-slack-sync.service
```

View live logs:

```bash
sudo journalctl -u discord-to-slack-sync.service -f
```

To remove the service and the venv:

```bash
scripts/uninstall_service.sh
```

If you prefer a different Python interpreter or venv location, edit [systemd/discord-to-slack-sync.service](systemd/discord-to-slack-sync.service) and `scripts/install_service.sh` accordingly.
