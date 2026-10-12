"use client";
import { AccountInsights } from "./v4-context";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { ComponentType } from "react";
import type { Composition, Node } from "@/lib/v4-composition";
import { TaskEditor, TransactionReview, type ComponentProps } from "./v4-editors";
import {
  AmountSimulator,
  BalanceProjection,
  FundingOptions,
  RecipientDetails,
  StockEstimate,
} from "./v4-analysis";

const renderers: Record<string, ComponentType<ComponentProps>> = {
  AccountInsights,
  TaskEditor,
  TransactionReview,
  AmountSimulator,
  BalanceProjection,
  FundingOptions,
  RecipientDetails,
  StockEstimate,
};
export function V4Workspace({ composition, ...props }: ComponentProps & { composition: Composition }) {
  const reduced = useReducedMotion();
  const root = composition.spec.elements[composition.spec.root];
  const ids = [...(root.slots?.primary ?? []), ...(root.slots?.tasks ?? []), ...(root.slots?.context ?? [])];
  const groups = ids.map((id) => ({
    node: composition.nodes.find((n) => n.id === id)!,
    leaves: [] as Node[],
  }));
  for (const group of groups) {
    const element = composition.spec.elements[group.node.id];
    group.leaves = renderers[group.node.recipe.component]
      ? [group.node]
      : Object.values(element.slots ?? {})
          .flat()
          .map((id) => composition.nodes.find((n) => n.id === id)!);
  }
  const positions: { node: Node; group: Node; row: number; column: string }[] = [];
  let row = 1;
  for (let i = 0; i < groups.length; i++) {
    const group = groups[i],
      next = groups[i + 1];
    const pairTasks =
      composition.layout === "split" &&
      group.node.recipe.component === "TaskGroup" &&
      next?.node.recipe.component === "TaskGroup";
    if (pairTasks) {
      group.leaves.forEach((node, j) =>
        positions.push({
          node,
          group: group.node,
          row: row + j,
          column: "1 / 7",
        }),
      );
      next.leaves.forEach((node, j) =>
        positions.push({
          node,
          group: next.node,
          row: row + j,
          column: "7 / 13",
        }),
      );
      row += Math.max(group.leaves.length, next.leaves.length);
      i++;
      continue;
    }
    const paired =
      composition.layout !== "stacked" &&
      group.node.recipe.component === "DecisionGroup" &&
      group.leaves.length === 2;
    group.leaves.forEach((node, j) =>
      positions.push({
        node,
        group: group.node,
        row: paired ? row : row + j,
        column: paired ? (j === 0 ? "1 / 7" : "7 / 13") : "1 / 13",
      }),
    );
    row += paired ? 1 : group.leaves.length;
  }
  return (
    <div className={`v4-grid v4-layout-${composition.layout}`} data-source={composition.source}>
      <AnimatePresence initial={false} mode="popLayout">
        {positions.map(({ node, group, row, column }, index) => {
          const Component = renderers[node.recipe.component];
          const key = `${node.recipe.component}:${node.recipe.action ?? "all"}`;
          return (
            <motion.section
              key={key}
              layout={reduced ? false : "position"}
              className={`v4-panel v4-panel-${node.recipe.component} v4-variant-${node.recipe.variant}`}
              style={{ gridRow: row, gridColumn: column }}
              data-component={node.recipe.component}
              data-component-key={key}
              data-group={`${group.recipe.component}:${group.recipe.action ?? "all"}`}
              data-variant={node.recipe.variant}
              initial={reduced ? false : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: reduced ? 0 : -4 }}
              transition={{
                duration: reduced ? 0 : 0.2,
                ease: [0.22, 1, 0.36, 1],
                opacity: {
                  duration: reduced ? 0 : 0.15,
                  delay: reduced ? 0 : Math.min(index * 0.025, 0.075),
                },
                layout: { duration: reduced ? 0 : 0.24 },
              }}
            >
              <Component {...props} action={node.recipe.action} variant={node.recipe.variant} />
            </motion.section>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
