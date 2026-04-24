# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Meta-Agent, please report it
privately. **Do not open a public GitHub issue.**

Email: **folkenai21@gmail.com**

Please include:

- A description of the issue and the potential impact
- Steps to reproduce, or a proof of concept
- The affected version or commit SHA
- Any suggested mitigation, if you have one

You can expect an initial response within 7 days. Once the issue is confirmed
and a fix is available, a coordinated disclosure timeline will be agreed with
the reporter.

## Supported versions

This project is in active development and only the `main` branch is supported.
Fixes are not backported to older tags.

## Scope

In scope:

- The Meta-Agent scaffolder and generated-agent templates in this repository
- The FastAPI server, plugin chain, and infrastructure included in the
  production skeleton

Out of scope:

- Vulnerabilities in third-party dependencies (report upstream)
- Issues requiring physical access or a compromised developer machine
- Social-engineering or phishing attacks against maintainers
- Leaked API keys in user-generated agents (rotate the key and review your
  `.env` handling)

## Handling your own secrets

Meta-Agent never logs API keys, but generated agents may call external services.
You are responsible for:

- Keeping `.env` out of version control (it is in `.gitignore` by default)
- Rotating any key you suspect has been exposed
- Running generated agents in an environment with least-privilege credentials
