# Demo dataset

`sun_hydraulics.yaml` describes a small Peruvian distributor of Sun
Hydraulics components. It is meant for an Odoo created **without Odoo's own
demo data**: `just odoo-fresh` drops the local Odoo and the application
database, installs the modules with `--without-demo=all`, generates the bot
API key, points the addon at the director, loads this dataset, checks that
nothing else is there (`just odoo-check`) and recreates the agents. Running
`just odoo-seed` again changes nothing: every record has a natural key.

What it creates:

- The company (renamed), one warehouse (`WH`). The dataset is in English (`language: en_US`)
  and so are the suppliers, so every email the agents write is in English.
- 30 products in five categories under `Hydraulics / …`: 12 cartridges
  (counterbalance, relief, needle, flow control, check, directional,
  reducing, logic), 6 bodies and manifolds, 4 coils, 4 seal kits, 4 hoses
  and fittings. Part numbers follow Sun's model-code scheme; prices are
  plausible list prices, not quotes.
- Three suppliers with `product.supplierinfo` terms and different delivery
  behaviour: **Proveedor Hidraulica** (the demo mailbox the agents write to,
  every product, 60 % on time), **Hidráulica Alterna SAC** (faster and dearer,
  85 % on time) and **Importadora del Sur SAC** (cheapest, minimum quantities,
  60 days and often late). The last two use `+alias` addresses of the demo
  mailbox, so their requests arrive there and the demo can answer for them.
- Four customers with demand weights.
- 24 months of demand as delivered sales orders from per-product profiles:
  stable, trending, seasonal (`campaign` peaks before the April and October
  mining shutdowns), intermittent (`pattern: intermittent`) and two products
  launched this year (`since_months`).
- 24 months of purchases with on-time, late and partial receipts, so OTIF
  and observed lead times differ per supplier.
- Reorder rules for the class A products only (top 60 % of revenue), two of
  them deliberately wrong and one missing (`rule_flaws`); the planner
  proposes the rest.

Day one is designed to create work (`today_weeks` per product, applied as an
inventory adjustment dated today, after the history):

- About a dozen products below their reorder point, two at zero inside the
  lead time, two seal kits entering the October season, one slow mover
  overstocked: the first daily plan proposes purchases and rules.
- Seven confirmed orders at every board stage: far out, due soon, late
  (one from the importer), two received and waiting for their invoice, each
  with a draft vendor bill (`F001-000201` matches; `F001-000198` bills the
  first line 3 % above the order).
- Three RFQs: two drafts and one sent six days ago to Proveedor Hidraulica,
  so the follow-up job has something to chase.

The history is generated from the profiles with the RNG `seed`, so two
machines produce the same two years and, from an empty database, the same
record ids. Seeded records carry natural keys (`default_code`, partner
email or name, `client_order_ref` `SEED-SO-…`, `sc_external_ref`
`seed-po-…`, `seed-open-…`, `seed-rfq-…`, bill `ref`) that the loader checks
before creating anything.

Options: `--only catalogue|stock|demand|supply`, `--months N`, `--seed N`,
`--dry-run`. After a fresh load, refresh the test cassettes with
`just odoo-record` and `just llm-record`.
