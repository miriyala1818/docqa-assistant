**v1 → v2.** v1 was tuned on dev and then run once on test (the only blind run; archived as
`reports/EVALUATION_REPORT_v1_blind_test.md`). Its failure analysis showed four design weaknesses, which were
fixed with general rules (no question-specific glossary entries or labels were added for test questions):

1. **Sentence window credit.** When a sentence pair scores high together, both sentences now get the credit
   (v1 credited only the second one, so "Invitations expire after 7 days" lost to the sentence after it).
2. **Evidence gate.** The gate now passes if the best passage *or* the best single sentence is strong enough.
   A long table chunk can score low as a whole while one row answers exactly ("How many projects can I create?").
3. **Clarification options.** Once the strongest evidence triggers a clarification, options are collected from
   all relevant evidence for that quantity (v1 dropped the Enterprise support row).
4. **Glossary matching.** Multi-word glossary phrases also match when all their content words appear in the
   question, not only as an exact phrase.

Thresholds were then re-tuned on dev only (`max_units` was added to the grid). **Because the test split was
inspected between v1 and v2, the v2 test numbers are no longer blind and are optimistic;** the v1 numbers are
the unbiased estimate.
