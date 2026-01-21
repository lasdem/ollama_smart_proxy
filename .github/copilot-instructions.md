# Copilot / Agent Instructions

Quick, actionable guidance to get an AI coding agent productive in this repo.

## Agent instructions
- When we start a fresh session, start by reading all files in docs, src, tests and root folders.
- The plan is in TODO.md, make sure to study it and update it along with changes.

## Big picture (what this project is)
This project is a smart proxy server for Ollama, built with FastAPI. It manages incoming requests, prioritizes them based on various factors, and routes them to Ollama backend. Key features include request ID tracking, enhanced logging, analytics, and deployment via Docker. The goal is to optimize request handling for Ollama while providing insights through logging and analytics.

## Developer workflows (commands & quickchecks) 🔧
- Environment First Time Setup:
  - `conda activate ./.conda` (project expects WSL/Ubuntu workflows)
  - `pip install -r requirements.txt`
- First run / manual runs:
  - `./.conda/bin/python smart_proxy.py` - runs the proxy server
- Testing:
  - `./.conda/bin/pytest` — runs all tests
- Configuration:
  - `.env` file in root for environment variables.

## Known quirks & helpful notes ⚠️
- Sometimes when executing commands the agent does not see the results and it should try again.
- changes are documented in docs/TODO.md and docs/changelog/*.md
- tests are in the tests/ folder and should be updated along with code changes.
- When running the proxy server, it will not exit and you cannot send commands to the same terminal. You must exit the proxy process first (Ctrl+C) before running new commands.
- Always use the conda environment: `./.conda/bin/python` and `./.conda/bin/pytest`
- Do NOT cd into the src directory when running commands - always run from the root directory