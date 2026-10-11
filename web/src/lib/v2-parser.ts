// Historical filename retained; this module now supplies mock records only.
import { type Accounts } from "./contracts";
import { exampleAccounts } from "./examples";

// Explicit mock banking records; never sent to validation, signing or execution.
export const v2Accounts: Accounts = {
  ...exampleAccounts,
  payees: [
    ...exampleAccounts.payees,
    { payee_id: "pay_8892", name: "John Smith", label: "John Smith ...8892" },
  ],
};
