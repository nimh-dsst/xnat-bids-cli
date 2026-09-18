# `xnatbidscli login`

Interactively collects credentials, verifies them against the server, and writes them to disk.

1. Prompts for the XNAT server URL in plain text (e.g., `https://xnat-server.domain.edu`).
2. Prompts for the username in plain text.
3. Prompts for the password via `getpass.getpass` so the characters are not echoed.
4. Connects to the server with PyXNAT and performs an authenticated request (`select.projects().get()`) to verify the credentials. Auth failures, connection failures, and other errors exit with distinct messages.
5. On success, writes `~/.xnatbidscli/credentials.cfg` with mode `0o600`.

```bash
xnatbidscli login
```

Re-running `xnatbidscli login` overwrites the stored credentials.

## Migrating from `xnatcli`

If credentials were previously saved by the pre-rename `xnatcli` package (`~/.xnatcli/credentials.cfg`), any command that needs credentials moves that file to `~/.xnatbidscli/credentials.cfg` automatically on first use, printing a one-line confirmation. No manual steps or re-login are required.
