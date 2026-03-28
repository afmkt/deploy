🚀 Git SSH Deploy Tool

A lightweight CLI tool to sync a local Git repository to a remote server over SSH.
It automates repository setup, remote configuration, and deployment in a single command.


✨ Features
Validate local Git repository
Connect to remote server via SSH
Automatically create remote directories
Initialize a bare Git repository on the server
Add remote to local repository
Push local code to remote
Clone or update working directory on server
Idempotent (safe to run multiple times)


🧰 Tech Stack
Python 3
CLI: click
TUI: rich
SSH: paramiko
Git: system git (via subprocess)
Packaging: pyinstaller

## 🚦 Usage

### 1. Build the Executable

To build the standalone `deploy` executable, run:

```sh
./scripts/build.sh
```

The executable will be created in the `dist/` directory as `dist/deploy`.

### 2. Run the Deploy Tool

You can now use the tool directly:

```sh
./dist/deploy --repo-path . --host <remote_host> --username <user> --key <path_to_ssh_key> --deploy-path /var/repos
```

Or, for interactive mode (recommended):

```sh
./dist/deploy
```

#### Options

- `--repo-path`   : Path to your local Git repository (default: current directory)
- `--host`        : Remote server hostname or IP
- `--port`        : SSH port (default: 22)
- `--username`    : SSH username
- `--key`         : Path to SSH private key
- `--password`    : SSH password (not recommended; use key if possible)
- `--deploy-path` : Path on remote server for deployment (default: /var/repos)
- `--interactive/--no-interactive` : Enable/disable interactive prompts (default: interactive)

#### Example

```sh
./dist/deploy --repo-path . --host example.com --username alice --key ~/.ssh/id_ed25519 --deploy-path /var/repos
```

#### After Deployment

- Add the provided remote URL to your local repo:
  ```sh
  git remote add deploy <bare_repo_url>
  git push deploy main
  ```


# Common usage patterns


## PUSH: Sync Local Repo to Remote

To push (sync) your local repository to the remote server:

1. Make sure you have added the remote (if not already):
  ```sh
  git remote add deploy <bare_repo_url>
  # Or update if it already exists:
  git remote set-url deploy <bare_repo_url>
  ```
2. Push your local branch to the remote:
  ```sh
  git push deploy main  # or your branch name
  ```
3. (Optional) Use the deploy tool to automate setup and push:
  ```sh
  ./dist/deploy --repo-path . --host <remote_host> --username <user> --key <path_to_ssh_key> --deploy-path /var/repos
  ```

This will sync your local changes to the remote bare repository and update the working directory on the server.

4. (Final Step) On the remote server, make sure the working directory is updated to the latest version:
   - SSH into your remote server:
     ```sh
     ssh <user>@<remote_host>
     cd <working_directory_path>
     git fetch origin
     git checkout main  # or your branch name
     git pull origin main  # or your branch name
     ```
   - If the checkout or pull fails, check the error message and resolve any issues (e.g., missing branch, permissions, or repo not initialized). Report the error if you cannot resolve it.
```


## PULL: Sync Remote Repo to Local

To pull (sync) changes from the remote server to your local repository:

1. Make sure the remote is set up:
  ```sh
  git remote add deploy <bare_repo_url>
  # Or update if it already exists:
  git remote set-url deploy <bare_repo_url>
  ```
2. Pull the latest changes from the remote:
  ```sh
  git pull deploy main  # or your branch name
  ```

This will fetch and merge changes from the remote bare repository to your local branch.
```
