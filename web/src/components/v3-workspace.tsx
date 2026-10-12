"use client";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import type { ComponentType } from "react";
import type { Manifest } from "@/lib/v3-presentation";
import type { ComponentId } from "@/lib/v3-registry";
import { TaskEditor, TransactionReview, type ComponentProps } from "./v3-editors";
import {
  AccountComparison,
  AmountSimulator,
  BalanceProjection,
  CashFlow,
  InvestmentCost,
  InvestmentRisk,
  MultiSummary,
  RecipientCheck,
} from "./v3-analysis";

const renderers: Record<ComponentId, ComponentType<ComponentProps>> = {
  transfer_editor: TaskEditor,
  bill_editor: TaskEditor,
  stock_editor: TaskEditor,
  balance_projection: BalanceProjection,
  cash_flow: CashFlow,
  amount_simulator: AmountSimulator,
  recipient_check: RecipientCheck,
  account_comparison: AccountComparison,
  investment_cost: InvestmentCost,
  investment_risk: InvestmentRisk,
  multi_summary: MultiSummary,
  transaction_review: TransactionReview,
};
export function V3Workspace({ manifest, ...props }: ComponentProps & { manifest: Manifest }) {
  const reduced = useReducedMotion();
  const ordered = [...manifest.nodes].sort(
    (a, b) =>
      ({ primary: 0, secondary: 1, support: 2 })[a.slot] - { primary: 0, secondary: 1, support: 2 }[b.slot],
  );
  return (
    <LayoutGroup id="v3-workspace">
      <div
        className={`v3-composition v3-${manifest.layout} v3-${manifest.density}`}
        data-interaction={manifest.interaction}
      >
        <AnimatePresence mode="popLayout">
          {ordered.map((node, index) => {
            const Component = renderers[node.component];
            return (
              <motion.section
                key={node.key}
                layout={reduced ? false : "position"}
                layoutId={reduced ? undefined : node.key}
                className={`v3-module v3-slot-${node.slot} v3-size-${node.size}`}
                data-component={node.component}
                data-component-key={node.key}
                data-slot={node.slot}
                initial={reduced ? false : { opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: reduced ? 0 : -4 }}
                transition={{
                  duration: reduced ? 0 : 0.24,
                  ease: [0.22, 1, 0.36, 1],
                  opacity: {
                    duration: reduced ? 0 : 0.18,
                    delay: reduced ? 0 : Math.min(index * 0.035, 0.105),
                  },
                  layout: { duration: reduced ? 0 : 0.28 },
                }}
              >
                {node.slot === "primary" && (
                  <motion.span
                    aria-hidden="true"
                    className="v3-primary-marker"
                    layoutId={reduced ? undefined : "primary-marker"}
                    transition={{
                      duration: reduced ? 0 : 0.28,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                  />
                )}
                <Component {...props} action={node.action} />
              </motion.section>
            );
          })}
        </AnimatePresence>
      </div>
    </LayoutGroup>
  );
}
