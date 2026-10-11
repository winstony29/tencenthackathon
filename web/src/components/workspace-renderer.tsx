"use client";
import type { ReactNode } from "react";
import { Button } from "./ui/button";
import { ActionCard } from "./action-card";
import {
  composeWorkspace,
  eligibleLayouts,
  validateManifest,
  type Layout,
  type UIManifest,
} from "@/lib/composition";
import { editedActions, projectedBalances, type DraftEdits } from "@/lib/draft-editor";
import type { Accounts, Draft, Quote } from "@/lib/contracts";
import { displayMoney } from "@/lib/money";

type Props = {
  draft: Draft;
  scheduled?: boolean;
  manifest: UIManifest;
  edits: DraftEdits;
  accounts: Accounts | null;
  quotes: Record<string, Quote>;
  approved: Record<number, boolean>;
  disabled: boolean;
  onEdit: (index: number, field: string, value: string) => void;
  onApprove: (index: number) => void;
  onLayout: (layout: Layout) => void;
};
// A layout arranges registry components; it cannot create or approve an action.
const layoutRegistry: Record<Layout, { title: string; className: string }> = {
  focus: { title: "One clear move.", className: "composition-focus" },
  split: { title: "The task. The context.", className: "composition-split" },
  comparison: { title: "Compare your funding.", className: "composition-comparison" },
  timeline: { title: "See the plan over time.", className: "composition-timeline" },
  sequence: { title: "Two moves. One plan.", className: "composition-sequence" },
  canvas: { title: "Explore before you commit.", className: "composition-canvas" },
};
function LayoutFrame({
  layout,
  children,
  support,
}: {
  layout: Layout;
  children: ReactNode;
  support: ReactNode;
}) {
  return (
    <div className={`composition-grid ${layoutRegistry[layout].className}`}>
      <div className="composition-tasks">{children}</div>
      {support && <aside className="composition-support">{support}</aside>}
    </div>
  );
}
export function WorkspaceRenderer(props: Props) {
  const facts = { mode: props.manifest.mode, accounts: props.accounts, scheduled: props.scheduled ?? false };
  // Validation runs even for an externally supplied/model-selected manifest.
  let manifest: UIManifest;
  try {
    manifest = validateManifest(props.manifest, props.draft, facts);
  } catch {
    manifest = composeWorkspace(props.draft, facts);
  }
  const result = editedActions(props.draft, props.edits, props.accounts);
  const invalid = Object.keys(result.errors).length > 0;
  const balances = props.accounts ? projectedBalances(result.actions, props.accounts, props.quotes) : [];
  const support = manifest.supporting.includes("projection") ? (
    <section className="projection-panel" aria-label="Projected balances">
      <span className="eyebrow">AFTER YOUR PLAN</span>
      <h3>What remains.</h3>
      <p>Mock estimate, calculated separately for each account. No currency conversion or fees included.</p>
      {invalid ? (
        <p role="status">Fix the highlighted fields to update the projection.</p>
      ) : (
        balances.map((account) => (
          <div className="projected-account" key={account.account_id}>
            <span>{account.label}</span>
            <strong className={account.projected?.startsWith("-") ? "negative" : ""}>
              {account.projected !== null
                ? displayMoney(account.projected, account.currency)
                : "Quote unavailable"}
            </strong>
            <small>Current: {displayMoney(account.balance, account.currency)}</small>
          </div>
        ))
      )}
      {!props.accounts && <p>Load account information before exploring affordability.</p>}
      <p className="projection-disclaimer">
        A projection is an assumption, not bank approval. Balances in different currencies cannot be combined.
      </p>
    </section>
  ) : manifest.supporting.includes("recipient") ? (
    <section className="inference-panel">
      <span className="eyebrow">INFERRED FROM YOUR REQUEST</span>
      <h3>Recipient comes first.</h3>
      <p>Check the full name and account suffix. This mock identity still needs bank verification.</p>
      <span className="verification-status">Verification pending</span>
    </section>
  ) : manifest.supporting.includes("accounts") ? (
    <section className="inference-panel">
      <span className="eyebrow">FUNDING CONTEXT</span>
      <h3>Keep each currency separate.</h3>
      {props.accounts?.accounts.map((account) => (
        <div className="context-row" key={account.account_id}>
          <span>{account.label}</span>
          <strong>{displayMoney(account.balance, account.currency)}</strong>
        </div>
      ))}
      <p>Balances are mock read-only data; the backend checks ownership, funds and limits.</p>
    </section>
  ) : null;
  return (
    <section
      className="adaptive-composition"
      data-layout={manifest.layout}
      aria-label="Adaptive financial workspace"
    >
      <div className="composition-heading">
        <div>
          <span className="eyebrow">
            {manifest.mode === "decision" ? "DECISION WORKSPACE" : "TRANSACTION WORKSPACE"} /{" "}
            {manifest.source.toUpperCase()} SELECTION
          </span>
          <h2>
            {manifest.layout === "sequence"
              ? `${props.draft.actions.length} moves. One plan.`
              : layoutRegistry[manifest.layout].title}
          </h2>
        </div>
        <span className="workspace-index">{String(props.draft.actions.length).padStart(2, "0")} ACTIONS</span>
      </div>
      <div className="layout-options" aria-label="Workspace arrangement">
        {eligibleLayouts(props.draft, facts).map((layout) => (
          <Button
            size="sm"
            variant="ghost"
            key={layout}
            aria-pressed={manifest.layout === layout}
            onClick={() => props.onLayout(layout)}
          >
            {layout}
          </Button>
        ))}
      </div>
      <LayoutFrame layout={manifest.layout} support={support}>
        {[...manifest.tasks]
          .sort((a, b) => a.priority - b.priority)
          .map((task) => (
            <ActionCard
              key={task.action}
              action={result.actions[task.action]}
              index={task.action}
              quote={
                result.actions[task.action].type === "stock_order"
                  ? props.quotes[(result.actions[task.action] as { ticker: string }).ticker]
                  : undefined
              }
              editor={{
                edits: props.edits,
                accounts: props.accounts,
                error: result.errors[task.action],
                approved: !!props.approved[task.action],
                disabled: props.disabled,
                onEdit: props.onEdit,
                onApprove: props.onApprove,
              }}
            />
          ))}
      </LayoutFrame>
    </section>
  );
}
