---
title: Orbitly 3.2 Release Notes
version: "3.2"
effective_date: 2026-06-01
product: Orbitly
doc_type: release_notes
---

# Orbitly 3.2 Release Notes

Orbitly 3.2 was released on 1 June 2026 to all workspaces.

## API changes

- The default API rate limit has been increased from 100 to 300 requests per minute per API key.
- A new `/v2/exports` endpoint lets you start and monitor workspace exports programmatically.

## Security changes

- Web sessions now expire after 60 minutes of inactivity (previously 30 minutes).
- Two-factor authentication is now mandatory for Owners and Admins. Owners and Admins without 2FA are asked to enable it at their next sign-in.

## Data export

- Workspace exports now support the Parquet format in addition to CSV and JSON.

## Audit logs

- Audit log retention on the Pro plan has been extended to 180 days.

## Bug fixes

- Fixed an issue where due dates were shown in the wrong time zone in weekly email digests.
- Fixed duplicate notifications when a task was moved between projects.
