/**
 * What each approval kind carries in its payload, parsed leniently: the agents
 * own these shapes (see the ApprovalRequest builders), the UI only reads them.
 */
import { z } from "zod";

import type { Schemas } from "@/api/client";

export type Approval = Schemas["ApprovalView"];
export type ApprovalKind = "send_email" | "po_change" | "planning_run" | "escalation" | "vendor_bill" | "supplier_score" | "autonomy_change" | "award" | "negotiation_offer" | "partner_create" | "internal_request" | "price_list_update";

export const emailPayload = z
  .object({
    to: z.array(z.string()).default([]),
    subject: z.string().default(""),
    html_body: z.string().default(""),
    po_name: z.string().nullish(),
    draft_id: z.string().nullish(),
    web_link: z.string().nullish(),
    attachments: z.array(z.string()).default([]),
  })
  .loose();
export type EmailPayload = z.infer<typeof emailPayload>;

export const proposedChange = z
  .object({
    po_line_id: z.number(),
    product: z.string().default(""),
    field: z.string(),
    before: z.union([z.string(), z.number()]).nullish(),
    after: z.union([z.string(), z.number()]).nullish(),
    source: z.string().nullish(),
    confidence: z.number().nullish(),
    needs_review: z.boolean().default(false),
    review_reason: z.string().nullish(),
    schedule: z.array(z.object({ qty: z.number(), date: z.string().nullish(), date_raw: z.string().nullish() }).loose()).default([]),
  })
  .loose();
export type ProposedChange = z.infer<typeof proposedChange>;

export const changePayload = z
  .object({
    po_name: z.string().nullish(),
    summary: z.string().nullish(),
    changes: z.array(proposedChange).default([]),
    needs_review: z.number().default(0),
  })
  .loose();
export type ChangePayload = z.infer<typeof changePayload>;

export const planPayload = z
  .object({
    run_id: z.string(),
    as_of: z.string().nullish(),
    warehouse_code: z.string().nullish(),
    summary: z.string().nullish(),
    totals: z.record(z.string(), z.number()).default({}),
    exceptions: z
      .array(
        z
          .object({
            line_id: z.string(),
            product_ref: z.string().default(""),
            exception: z.string().nullish(),
            action: z.string().nullish(),
            order_qty: z.number().nullish(),
            explanation: z.string().nullish(),
          })
          .loose(),
      )
      .default([]),
    lines: z
      .array(
        z
          .object({
            line_id: z.string(),
            product_ref: z.string().default(""),
            action: z.string().nullish(),
            order_qty: z.number().nullish(),
            proposed_min: z.number().nullish(),
            proposed_max: z.number().nullish(),
            supplier: z.string().nullish(),
          })
          .loose(),
      )
      .default([]),
  })
  .loose();
export type PlanPayload = z.infer<typeof planPayload>;

export const escalationPayload = z
  .object({
    reason: z.string().default(""),
    details: z.record(z.string(), z.unknown()).default({}),
    case_id: z.string().nullish(),
    case_kind: z.string().nullish(),
    po_name: z.string().nullish(),
    trace_url: z.string().nullish(),
    history: z.array(z.string()).default([]),
    web_link: z.string().nullish(),
  })
  .loose();
export type EscalationPayload = z.infer<typeof escalationPayload>;

export const billLine = z
  .object({
    po_line_id: z.number().nullish(),
    product: z.string().nullish(),
    invoice_description: z.string().default(""),
    invoice_qty: z.number().nullish(),
    invoice_price: z.number().nullish(),
    po_qty: z.number().nullish(),
    po_price: z.number().nullish(),
    received_qty: z.number().nullish(),
    status: z.string().default("ok"),
    note: z.string().nullish(),
  })
  .loose();

export const billPayload = z
  .object({
    po_name: z.string().nullish(),
    verdict: z.string().default("hold"),
    reasons: z.array(z.string()).default([]),
    invoice: z
      .object({
        supplier_name: z.string().nullish(),
        invoice_number: z.string().nullish(),
        invoice_date: z.string().nullish(),
        currency: z.string().nullish(),
        subtotal: z.number().nullish(),
        tax: z.number().nullish(),
        total: z.number().nullish(),
      })
      .loose()
      .default({}),
    lines: z.array(billLine).default([]),
    expected_subtotal: z.number().nullish(),
    existing_bill_name: z.string().nullish(),
  })
  .loose();
export type BillPayload = z.infer<typeof billPayload>;

export const scoreRow = z
  .object({
    partner_id: z.number(),
    partner_name: z.string(),
    score: z.number(),
    otif: z.number().nullish(),
    lead_time_mean_days: z.number().nullish(),
    lead_time_sigma_days: z.number().nullish(),
    promise_drift_days: z.number().nullish(),
    response_hours_median: z.number().nullish(),
    quality_rate: z.number().nullish(),
    price_cv: z.number().nullish(),
    samples: z.record(z.string(), z.number()).default({}),
    scorecard: z.string().nullish(),
    trends: z.array(z.string()).default([]),
  })
  .loose();

export const scoresPayload = z
  .object({
    run_id: z.string().nullish(),
    period: z.object({ start: z.string().nullish(), end: z.string().nullish() }).loose().nullish(),
    scores: z.array(scoreRow).default([]),
  })
  .loose();
export type ScoresPayload = z.infer<typeof scoresPayload>;

export const autonomyRule = z
  .object({
    id: z.string(),
    kind: z.string().default("*"),
    level: z.string().default("approve"),
    revert_hours: z.number().default(24),
    note: z.string().default(""),
    enabled: z.boolean().default(true),
    when: z.record(z.string(), z.unknown()).default({}),
  })
  .loose();

export const autonomyChangePayload = z
  .object({
    policy: z.object({ rules: z.array(autonomyRule).default([]) }).loose().default({ rules: [] }),
    widened: z.array(z.string()).default([]),
    requested_by: z.string().nullish(),
    requested_by_name: z.string().nullish(),
    note: z.string().nullish(),
  })
  .loose();
export type AutonomyChangePayload = z.infer<typeof autonomyChangePayload>;

export const quoteLine = z
  .object({
    product_id: z.number(),
    product: z.string().default(""),
    qty: z.number().default(1),
    price_unit: z.number().nullish(),
    landed_unit: z.number().nullish(),
    lead_days: z.number().nullish(),
    min_qty: z.number().default(0),
  })
  .loose();

export const comparedQuote = z
  .object({
    partner_id: z.number(),
    partner_name: z.string(),
    po_name: z.string().nullish(),
    source: z.string().default("none"),
    currency: z.string().nullish(),
    lines: z.array(quoteLine).default([]),
    total: z.number().nullish(),
    lead_days: z.number().nullish(),
    score: z.number().nullish(),
    complete: z.boolean().default(true),
    rank: z.number().default(0),
    reasons: z.array(z.string()).default([]),
    recommended: z.boolean().default(false),
    first_time_supplier: z.boolean().default(false),
  })
  .loose();
export type ComparedQuote = z.infer<typeof comparedQuote>;

export const awardPayload = z
  .object({
    round_id: z.number().nullish(),
    mode: z.string().default("round"),
    source_po_name: z.string().nullish(),
    recommended_partner_id: z.number().nullish(),
    comparison: z
      .object({
        round_id: z.number().default(0),
        basket: z.array(quoteLine).default([]),
        quotes: z.array(comparedQuote).default([]),
        recommended_partner_id: z.number().nullish(),
        line_awards: z
          .array(z.object({ product_id: z.number(), product: z.string().default(""), partner_id: z.number(), partner_name: z.string(), po_name: z.string().nullish(), landed_unit: z.number().nullish(), reasons: z.array(z.string()).default([]) }).loose())
          .default([]),
        recommendation: z.string().default(""),
        freight_pct: z.number().default(0),
        invited: z.number().default(0),
        replied: z.number().default(0),
      })
      .loose(),
  })
  .loose();
export type AwardPayload = z.infer<typeof awardPayload>;

export const offerPayload = z
  .object({
    po_name: z.string(),
    partner_id: z.number(),
    partner_name: z.string(),
    product_id: z.number(),
    product: z.string(),
    qty: z.number().default(1),
    currency: z.string().nullish(),
    current_price: z.number(),
    target_price: z.number(),
    floor_price: z.number(),
    offered_price: z.number(),
    cap_pct: z.number().default(0),
    round_no: z.number().default(1),
    max_rounds: z.number().default(1),
    basis: z.string().default(""),
    justification: z.string().default(""),
  })
  .loose();
export type OfferPayload = z.infer<typeof offerPayload>;

export const partnerPayload = z
  .object({
    sender_address: z.string().nullish(),
    suggested_name: z.string().default(""),
    web_link: z.string().nullish(),
    currency: z.string().nullish(),
    notes: z.string().nullish(),
    lines: z
      .array(
        z
          .object({
            product_ref: z.string().nullish(),
            description: z.string().default(""),
            qty: z.number().nullish(),
            unit_price: z.number().nullish(),
            currency: z.string().nullish(),
            lead_days: z.number().nullish(),
          })
          .loose(),
      )
      .default([]),
  })
  .loose();
export type PartnerPayload = z.infer<typeof partnerPayload>;

export const requestPayload = z
  .object({
    sender_address: z.string().nullish(),
    web_link: z.string().nullish(),
    need_date: z.string().nullish(),
    need_date_raw: z.string().nullish(),
    notes: z.string().nullish(),
    confidence: z.number().default(1),
    unmatched: z.number().default(0),
    items: z
      .array(
        z
          .object({
            description: z.string().default(""),
            product_ref: z.string().nullish(),
            qty: z.number().default(1),
            uom: z.string().nullish(),
            product_id: z.number().nullish(),
            product: z.string().nullish(),
            match_confidence: z.number().default(0),
            supplier_id: z.number().nullish(),
            supplier_name: z.string().nullish(),
            unit_price: z.number().nullish(),
            currency: z.string().nullish(),
          })
          .loose(),
      )
      .default([]),
  })
  .loose();
export type RequestPayload = z.infer<typeof requestPayload>;

export const priceListPayload = z
  .object({
    partner_id: z.number(),
    partner_name: z.string().default(""),
    source: z.string().default(""),
    currency: z.string().nullish(),
    web_link: z.string().nullish(),
    changed: z.number().default(0),
    unmatched: z.number().default(0),
    rows: z
      .array(
        z
          .object({
            code: z.string(),
            description: z.string().default(""),
            product_id: z.number().nullish(),
            product: z.string().nullish(),
            current_price: z.number().nullish(),
            new_price: z.number(),
            currency: z.string().nullish(),
            min_qty: z.number().default(0),
            lead_days: z.number().nullish(),
            change_pct: z.number().nullish(),
            matched: z.boolean().default(false),
          })
          .loose(),
      )
      .default([]),
  })
  .loose();
export type PriceListPayload = z.infer<typeof priceListPayload>;

const KNOWN: ReadonlySet<string> = new Set(["send_email", "po_change", "planning_run", "escalation", "vendor_bill", "supplier_score", "autonomy_change", "award", "negotiation_offer", "partner_create", "internal_request", "price_list_update"]);

export function kindOf(approval: Approval): ApprovalKind | "other" {
  return KNOWN.has(approval.kind) ? (approval.kind as ApprovalKind) : "other";
}

/** Age in whole days from an ISO timestamp, for the "pending for N days" hint. */
export function daysSince(iso: string | null | undefined, now = new Date()): number {
  if (!iso) return 0;
  const then = new Date(iso).getTime();
  return Number.isNaN(then) ? 0 : Math.max(0, Math.floor((now.getTime() - then) / 86_400_000));
}
