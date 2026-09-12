# Demo dataset

`sun_hydraulics.yaml` describes a small Peruvian distributor of Sun
Hydraulics components. `just odoo-seed` loads it into the local Odoo as the
administrator; running it again changes nothing.

What it creates:

- The company (renamed, Spanish `es_PE` installed), one warehouse (`WH`).
- 14 products under `Hidráulica / …`: counterbalance, relief, needle, flow
  control, check, directional, reducing and logic cartridges, a T-11A line
  body, a 12 VDC coil and a T-11A seal kit. Part numbers follow Sun's
  model-code scheme; prices are plausible list prices, not quotes.
- Two suppliers: **Proveedor Hidraulica** (the phase 5 demo mailbox, every
  product, imported lead times) and **Sun Hydraulics Distribuidor Alterno**
  (no email on purpose, faster and dearer, 8 products), with
  `product.supplierinfo` terms.
- Four customers with demand weights.
- Stock on hand, reorder rules (three deliberately wrong, see `rule_flaws`),
  24 months of demand as delivered sales orders, 24 months of purchases with
  on-time, late and partial receipts, and three open incoming orders.

The history is generated from the per-product demand profiles with the RNG
`seed`, so two machines produce the same two years. Seeded records carry
natural keys (`default_code`, partner email or name, `client_order_ref`
`SEED-SO-…`, `sc_external_ref` `seed-po-…`) that the loader checks before
creating anything.

Options: `--only catalogue|stock|demand|supply`, `--months N`, `--seed N`,
`--dry-run`. A fresh demo: `just odoo-reset`, `just odoo-init`, `just odoo-seed`.
