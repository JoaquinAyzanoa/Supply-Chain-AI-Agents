# The ten-minute demo

A scripted day in the purchasing department, run on the live stack against the
demo supplier **Proveedor Hidraulica**. The department side is the real thing:
the same agents, approvals, Odoo events and emails as any other day. The world
side (the supplier answering, the warehouse receiving, accounting typing a bill)
is played by the demo so nobody has to leave the room.

Everything below assumes `just up`, a seeded Odoo (`just odoo-fresh`), a signed-in
bot mailbox (`just mail-login`) and a Control Tower approver.

## Before the room fills

1. **The supplier's mailbox.** The supplier's replies are sent by SMTP from the
   Hidraulica Gmail account. Create an app password for it and put, in `.env`:

   ```
   SC__DEMO__SMTP_USER=ventas.hidraulica.sc@gmail.com
   SC__DEMO__SMTP_PASSWORD=<app password>
   ```

   Without it, steps 2, 4 and 5 report what is missing instead of sending.
2. **The warehouse login.** The bot cannot validate receipts or type bills on
   purpose. The demo uses a second Odoo login for those two steps (locally the
   administrator):

   ```
   SC__DEMO__ODOO_LOGIN=admin
   SC__DEMO__ODOO_API_KEY=<that user's API key or password>
   ```

3. Restart the director (`docker compose ... up -d --no-deps director`) so it
   reads the new settings, then open **Demo** in the Control Tower. The page
   says what is still missing.
4. **Reset.** Click *Reset* on the Demo page (or `just demo-reset`). The late
   order goes back to its original date, the last round's RFQs are cancelled,
   the run's pending approvals are rejected, and a fresh order is confirmed for
   the receipt and the bill (a received order cannot be un-received).
5. Have four tabs open: Home, Board, Approvals, Demo. On a phone, the PWA with
   the Approvals inbox.

Two ways to drive it:

- **From the Demo page.** Tick *Decide the approvals for me* if you want the
  page to approve what each step raises; leave it off to decide live in the
  inbox (the better show). Press *Next* for each step; a step that is still
  waiting for the mailbox says so and can be pressed again.
- **From a terminal.** `just demo-pace` waits for Enter before each step and
  prints what to say; `just demo-auto` runs the whole thing unattended and
  decides the approvals itself (the rehearsal, and the timing check).

Every step's outcome is a line with links into the place where it shows.

## The script

Say the lines in your own words. *Look* is where the audience's eyes should be.

### 1. A late order gets a delivery-date request (about 1 min)

**Say.** "Proveedor Hidraulica is late on an order. Nobody in the department
noticed, because there is no department; the director did. It writes to the
supplier asking for a firm date, in the supplier's language, with the order's
own facts."

**Look.** Approvals: the email waits for a person. Open it: the draft, the
*Why* panel with the facts, the rule and the confidence. Approve it, or show
the Autonomy page where a rule would let reminders go alone.

### 2. The supplier answers with a new date (about 1.5 min)

**Say.** "The supplier replies from its own mailbox. The agent reads the date,
checks it against the order and proposes to move it. Odoo only changes after a
decision."

**Look.** Approvals: the order change with the old and the new date. Approve
it; on the Board the card leaves *late*. On the order panel: the email, linked,
opens in Outlook.

The reply takes a moment to travel from Gmail to the bot's inbox; the step
polls the mailbox for up to two and a half minutes and says "run the step
again" if it is slower.

### 3. A stockout risk starts a quote round (about 1 min)

**Say.** "Meanwhile the planner sees a product running out inside its lead
time with nothing on order. One click asks every supplier who lists it for a
quote: one RFQ per supplier, grouped as alternatives in Odoo, each on its own
case."

**Look.** Risk: the product at the top with its 30-day odds. Board: the RFQs.
Approvals: the RFQ emails (approve them, or let a rule send RFQs alone).

### 4. A quote above target gets a counter-offer (about 1.5 min)

**Say.** "Hidraulica quotes twelve percent above what we last paid. The
sourcing agent proposes a counter-offer within the buyer's cap, with the
evidence: the last price, the best competing price. A person approves it
before it goes."

**Look.** Approvals: the counter-offer card, then the email it becomes.

### 5. The supplier accepts and the round is awarded (about 1.5 min)

**Say.** "The supplier accepts. The comparison weighs landed cost, lead time
and the supplier's score, recommends, and a person awards. Odoo confirms the
order and the order goes out, with the PDF."

**Look.** Approvals: the award with the comparison table. Board: the order
confirmed. Supplier 360: the round in the supplier's history.

### 6. A short receipt gets a discrepancy report (about 1 min)

**Say.** "The warehouse receives the demo order, but fewer units than ordered.
The logistics agent reconciles the receipt against the order and drafts the
discrepancy for the supplier."

**Look.** Board: the receipt card with the discrepancy. Approvals: the report.

### 7. An invoice with a price variance (about 1 min)

**Say.** "Accounting types the supplier's bill. One line is three percent above
the order. The matching agent finds it against the order and the receipt, and
asks before anything is posted."

**Look.** Approvals: the vendor bill with the variance per line.

### 8. The briefing the next morning (about 1 min)

**Say.** "Every morning at 07:30 the director writes what happened, what ran
alone and what needs a decision, with the facts behind every line, and emails
it. Ask it anything about the desk."

**Look.** Briefing: the paragraph and the sections. Home: the day at a glance,
the AI performance page for the automation rate of the day. Assistant: one
question, with citations that open the records.

## When something does not go to plan

- A step says **waiting**: the mailbox or an agent has not answered yet. Press
  it again; nothing is resent. Check `just mail-check` and the Runs page.
- A step says **failed** with a setting name: see *Before the room fills*.
- The approvals a step raised are listed on its outcome; decide them in the
  inbox and press the step again if it stopped there.
- `just demo-reset` at any point returns to the start; what Odoo cannot undo
  (a validated receipt) is replaced by a fresh demo order.

## What is real and what is not

Real: every email the department sends (they reach the Hidraulica mailbox),
every Odoo record, every agent run and its cost, every approval and its
reasoning. Played: the supplier's three replies (sent from its real mailbox by
the demo), the receipt and the bill (typed through Odoo with a warehouse login).
No email text is stored anywhere by the demo; the outcomes keep ids and links.
