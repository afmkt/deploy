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