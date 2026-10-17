import { draftSchema, type Draft } from "./contracts";
import { editedActions, updateEdit, type DraftEdits } from "./draft-editor";
import { v2Accounts } from "./v2-parser";
export type Surface = "conversation" | "workspace" | "review" | "result";
export type ConversationState = {
  surface: Surface;
  draft: Draft | null;
  edits: DraftEdits;
  mode: "transaction" | "decision";
  revision: number;
  transcript: string;
  error: string;
  approved: Record<number, boolean>;
};
export const initialConversation: ConversationState = {
  surface: "conversation",
  draft: null,
  edits: {},
  mode: "transaction",
  revision: 0,
  transcript: "",
  error: "",
  approved: {},
};
export type ConversationEvent =
  | { type: "handoff"; draft: Draft; mode: "transaction" | "decision" }
  | { type: "edit"; index: number; field: string; value: string }
  | { type: "return" }
  | { type: "review" }
  | { type: "approve"; index: number }
  | { type: "finish" }
  | { type: "reset" };
export function conversationReducer(state: ConversationState, event: ConversationEvent): ConversationState {
  switch (event.type) {
    case "handoff": {
      if (
        !draftSchema.safeParse(event.draft).success ||
        event.draft.actions.some((a) => a.type === "fx_conversion" || a.type === "account_transfer") || // V2 has no layout for these
        Object.keys(editedActions(event.draft, {}, v2Accounts).errors).length
      )
        return {
          ...state,
          error: "Await a complete valid Winston output. Missing details are resolved upstream.",
        };
      // Preserve only edits to unchanged fields of the same task. Upstream changes win.
      const edits: DraftEdits = {};
      if (state.draft?.draft_id === event.draft.draft_id) {
        event.draft.actions.forEach((action, index) => {
          const old = state.draft!.actions[index];
          if (!old || old.type !== action.type) return;
          const identity =
            action.type === "transfer"
              ? "to_payee_id"
              : action.type === "stock_order"
                ? "ticker"
                : "biller_id";
          const before = old as unknown as Record<string, unknown>;
          const after = action as unknown as Record<string, unknown>;
          if (before[identity] !== after[identity]) return;
          for (const [field, value] of Object.entries(state.edits[index] ?? {})) {
            if (before[field] === after[field]) edits[index] = { ...edits[index], [field]: value };
          }
        });
      }
      return {
        ...initialConversation,
        draft: event.draft,
        mode: event.mode,
        edits,
        surface: "workspace",
        transcript: event.draft.transcript,
        revision: state.revision + 1,
      };
    }
    case "reset":
      return { ...initialConversation, revision: state.revision + 1 };
    case "return":
      return { ...state, surface: state.draft ? "workspace" : "conversation", approved: {}, error: "" };
    case "edit": {
      if (!state.draft || state.surface !== "workspace") return state;
      return {
        ...state,
        edits: updateEdit(state.edits, event.index, event.field, event.value),
        approved: {},
        revision: state.revision + 1,
        error: "",
      };
    }
    case "review": {
      if (
        !state.draft ||
        state.surface !== "workspace" ||
        Object.keys(editedActions(state.draft, state.edits, v2Accounts).errors).length
      )
        return state;
      return { ...state, surface: "review", approved: {}, error: "" };
    }
    case "approve":
      return state.surface === "review" && state.draft?.actions[event.index]
        ? { ...state, approved: { ...state.approved, [event.index]: !state.approved[event.index] } }
        : state;
    case "finish":
      return state.surface === "review" && state.draft?.actions.every((_, index) => state.approved[index])
        ? { ...state, surface: "result" }
        : state;
  }
}
