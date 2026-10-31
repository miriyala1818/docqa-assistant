---
title: Orbitly Usage Limits by Plan
version: "3.2"
effective_date: 2026-06-01
product: Orbitly
doc_type: reference
---

# Usage Limits by Plan

The following limits apply to each workspace.

| Limit | Basic | Pro | Enterprise |
|-------|-------|-----|------------|
| Members per workspace | 10 users | 250 users | Unlimited users |
| Projects | 20 projects | 500 projects | Unlimited projects |
| Storage per workspace | 10 GB | 100 GB | 1 TB |
| Maximum file upload size | 100 MB | 2 GB | 5 GB |
| API calls per day | 10,000 calls | 100,000 calls | 1,000,000 calls |

## What happens when you reach a limit

- Storage: new uploads are blocked. Owners and Admins are notified by email when the workspace reaches 80% and 100% of its storage.
- Members: you cannot send new invitations until you remove members or upgrade.
- API calls per day: further calls are rejected with error ORB-429 until the daily quota resets at 00:00 UTC.

## Increasing limits

Pro and Enterprise workspaces can buy additional storage in blocks of 50 GB for $10 per month from Settings > Billing > Add-ons. Other limits can be increased by upgrading the plan.
