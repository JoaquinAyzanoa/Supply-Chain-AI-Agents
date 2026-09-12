# Odoo (local)

Odoo 18 Community runs in docker compose (`infra/compose.odoo.yml`). This
directory holds its configuration and our custom addon.

```
odoo/
├── config/odoo.conf   # mounted read-only into the container
└── addons/            # mounted as /mnt/extra-addons; sc_agents lives here (phase 1, story P1-S3)
```

## Commands

| Recipe | What it does |
|--------|--------------|
| `just up` | starts `odoo-db` and `odoo` with the rest of the stack |
| `just odoo-init` | creates database `scai` with demo data and installs the modules below (idempotent; re-running re-installs nothing) |
| `just odoo-upgrade [module]` | upgrades a module after code changes (default `sc_agents`) |
| `just odoo-configure` | sets the system parameters the addon uses to reach the director (`sc_agents.director_url`, `sc_agents.events_secret`) |
| `just odoo-shell` | Odoo Python shell against `scai` |
| `just odoo-reset` | stops Odoo and deletes its database and filestore volumes |
| `just logs odoo` | follows the server log |

Web UI: http://localhost:8069, database `scai`, user `admin` / `admin`.
Override the host port with `SC_ODOO_PORT`.

## Modules installed by `odoo-init`

| Module | Used for |
|--------|----------|
| `contacts` | suppliers and their email addresses (used to link inbound mail) |
| `mail` | chatter and activities; no mail servers are configured, Graph handles email |
| `product`, `purchase` | RFQ / PO, `purchase.order.line.date_planned`, `product.supplierinfo` |
| `stock`, `purchase_stock` | warehouses, reorder rules, receipts, lead times |
| `sale_management` | confirmed demand for the planner |
| `purchase_requisition` | calls for tender (one RFQ to several suppliers) |
| `base_automation` | rules that emit signed events to the director when an order is confirmed or a receipt validated (phase 6) |

Also present: `account`, pulled in automatically as a dependency of
`purchase`. Phase 9 (invoice matching) uses it without any further install.

Not installed by default:

- `mrp`: only if the company manufactures. If so, add it to `ODOO_MODULES` in
  the `Justfile` and the planner reads bill-of-material demand in phase 7.

Demo data is loaded for development and tests. Production databases must be
created without it (`--without-demo=all`).
