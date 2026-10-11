import type { Draft } from "./contracts";
import { editedActions, updateEdit, type DraftEdits } from "./draft-editor";
import {
  amendment,
  completeTransfer,
  parseDemoCommand,
  refineTransfer,
  v2Accounts,
  type PartialTransfer,
} from "./v2-parser";
export type Surface =
  "conversation" | "interpreting" | "partial" | "workspace" | "refinement" | "review" | "result";
export type ConversationState = {
  surface: Surface;
  resume: Surface;
  draft: Draft | null;
  partial: PartialTransfer | null;
  edits: DraftEdits;
  mode: "transaction" | "decision";
  revision: number;
  transcript: string;
  error: string;
  approved: Record<number, boolean>;
};
export const initialConversation: ConversationState = {
  surface: "conversation",
  resume: "conversation",
  draft: null,
  partial: null,
  edits: {},
  mode: "transaction",
  revision: 0,
  transcript: "",
  error: "",
  approved: {},
};
export type ConversationEvent =
  | { type: "load"; draft: Draft | null; partial: PartialTransfer | null; mode: "transaction" | "decision" }
  | { type: "begin" }
  | { type: "command"; text: string }
  | { type: "edit"; index: number; field: string; value: string }
  | { type: "clarify"; field: "amount" | "recipient" | "source"; value: string }
  | { type: "chat" }
  | { type: "return" }
  | { type: "review" }
  | { type: "approve"; index: number }
  | { type: "finish" }
  | { type: "reset" };
export function conversationReducer(state: ConversationState, event: ConversationEvent): ConversationState {
  switch (event.type) {
    case "load":
      return {
        ...initialConversation,
        draft: event.draft,
        partial: event.partial,
        mode: event.mode,
        revision: state.revision + 1,
        transcript: event.draft?.transcript ?? "Mock Winston output: transfer details pending",
        surface: event.partial ? "partial" : event.draft ? "workspace" : "conversation",
      };
    case "begin":
      return {
        ...state,
        resume: state.surface === "refinement" ? state.resume : state.surface,
        surface: "interpreting",
        error: "",
      };
    case "reset":
      return { ...initialConversation, revision: state.revision + 1 };
    case "chat":
      return {
        ...state,
        resume: state.surface,
        surface: state.draft || state.partial ? "refinement" : "conversation",
        error: "",
      };
    case "return":
      return {
        ...state,
        surface: state.draft ? "workspace" : state.partial ? "partial" : "conversation",
        error: "",
      };
    case "command": {
      const revision = state.revision + 1;
      if (state.draft) {
        const change = amendment(event.text, state.draft);
        if (change.error) return { ...state, surface: "refinement", error: change.error };
        if (change.mode) return { ...state, surface: "workspace", mode: change.mode, error: "" };
        let edits = state.edits;
        for (const [field, value] of Object.entries(change.fields ?? {}))
          edits = updateEdit(edits, change.index!, field, value);
        return {
          ...state,
          edits,
          approved: {},
          revision,
          surface: "workspace",
          transcript: `${state.transcript}\n${event.text}`,
          error: "",
        };
      }
      if (state.partial) {
        const partial = refineTransfer(state.partial, event.text);
        if (partial) {
          const draft = completeTransfer(partial, `${state.transcript}\n${event.text}`, revision);
          return {
            ...state,
            partial: draft ? null : partial,
            draft,
            surface: draft ? "workspace" : "partial",
            revision,
            error: "",
          };
        }
        return {
          ...state,
          surface: "partial",
          error: "Use the recipient choices and amount field to complete this transfer.",
        };
      }
      const parsed = parseDemoCommand(event.text, revision);
      return {
        ...state,
        draft: parsed.draft ?? null,
        partial: parsed.partial ?? null,
        mode: parsed.mode,
        revision,
        transcript: event.text,
        surface: parsed.error ? "conversation" : parsed.partial ? "partial" : "workspace",
        error: parsed.error ?? "",
      };
    }
    case "clarify": {
      if (!state.partial) return state;
      const partial = { ...state.partial, [event.field]: event.value };
      const draft = completeTransfer(partial, state.transcript, state.revision + 1);
      return {
        ...state,
        partial: draft ? null : partial,
        draft,
        surface: draft ? "workspace" : "partial",
        revision: state.revision + 1,
        approved: {},
        error: "",
      };
    }
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
